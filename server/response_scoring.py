"""Rank checkpoint-drone pairs for responding to a detected object.

Runs on the machine that holds the database. Pure scoring: no database, no
HTTP, so it can be exercised on its own and the weights argued about without
a Postgres to hand.

A pair is one checkpoint used as the responding station and one drone flown
from it. Pairs are scored, not drones alone: a farther checkpoint holding a
fast long-range drone can beat a near one holding a weak one, and that only
shows up if the two are scored together.

Two stages, deliberately separate:

  1. Feasibility — a hard filter. A drone that cannot reach the object, is
     unavailable, is not in a ready status, or has too little battery or
     endurance is EXCLUDED with a stated reason. It is never merely ranked
     low, because a low rank still reads as "possible".
  2. Score — the survivors, out of 100, over weighted factors.

Every excluded pair keeps its reason, so the recommendation can say why the
best option is best rather than only that it won.
"""

import math

# --- feasibility -----------------------------------------------------------
# Statuses a drone may be dispatched from. Anything else is excluded and
# named, rather than being silently dropped.
READY_STATUSES = ("idle", "ready", "available", "standby", "armed")

# Below this the drone is not dispatched at all.
MIN_BATTERY_PERCENT = 20.0

# Flight time must cover airborne + out + back with this much left over.
ENDURANCE_RESERVE_MIN = 5.0

# --- scoring ---------------------------------------------------------------
# Weights need not total 100; the score is scaled by whatever they sum to.
WEIGHTS = {
    "eta": 35.0,            # how soon it can be overhead — usually decisive
    "range_margin": 20.0,   # reach left over beyond the distance flown
    "battery": 20.0,        # charge in hand
    "checkpoint_proximity": 15.0,   # how close the station is to the object
    "readiness": 10.0,      # availability and status
}

# An ETA at or beyond this scores 0 for the eta factor.
ETA_CEILING_MIN = 60.0

# A checkpoint at or beyond this from the object scores 0 for proximity.
CHECKPOINT_CEILING_M = 20000.0

# A drone is taken to belong to a checkpoint when it sits within this of it.
# Beyond it the pair is still scored, but flagged as not stationed there.
STATIONED_RADIUS_M = 1500.0

EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _number(value):
    """Return a real float, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result


def _clip01(value):
    if value is None:
        return 0.0
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def cruise_speed_kmh(drone):
    """Infer a cruise speed from the columns the table actually has.

    There is no speed column, but operational_range_km flown within
    max_flight_time_min implies one. Treated as a one-way reach, which is the
    conservative reading: if the range is meant as a round trip the real
    speed is higher and the ETA below is pessimistic, never optimistic.

    Returns None when either column is missing, and the caller then cannot
    compute an ETA.
    """
    range_km = _number(drone.get("operational_range_km"))
    flight_min = _number(drone.get("max_flight_time_min"))
    if not range_km or not flight_min or range_km <= 0 or flight_min <= 0:
        return None
    return range_km / (flight_min / 60.0)


def evaluate_pair(checkpoint, drone, target_lat, target_lon):
    """Return the facts about flying ``drone`` from ``checkpoint`` to a target.

    Distances in metres, times in minutes. Values that cannot be derived are
    None, and feasibility below treats a missing value as disqualifying
    rather than assuming the best case.
    """
    drone_lat = _number(drone.get("latitude"))
    drone_lon = _number(drone.get("longitude"))
    cp_lat = _number(checkpoint.get("latitude"))
    cp_lon = _number(checkpoint.get("longitude"))

    facts = {
        "drone_to_target_m": None,
        "drone_to_checkpoint_m": None,
        "checkpoint_to_target_m": _number(checkpoint.get("distance_m")),
        "cruise_kmh": cruise_speed_kmh(drone),
        "travel_min": None,
        "eta_min": None,
        "range_margin_km": None,
        "endurance_needed_min": None,
        "stationed_at_checkpoint": None,
    }

    if drone_lat is not None and drone_lon is not None:
        facts["drone_to_target_m"] = haversine_m(drone_lat, drone_lon,
                                                 target_lat, target_lon)
        if cp_lat is not None and cp_lon is not None:
            facts["drone_to_checkpoint_m"] = haversine_m(
                drone_lat, drone_lon, cp_lat, cp_lon)
            facts["stationed_at_checkpoint"] = (
                facts["drone_to_checkpoint_m"] <= STATIONED_RADIUS_M)

    if facts["checkpoint_to_target_m"] is None and None not in (cp_lat, cp_lon):
        facts["checkpoint_to_target_m"] = haversine_m(cp_lat, cp_lon,
                                                      target_lat, target_lon)

    distance_km = (facts["drone_to_target_m"] / 1000.0
                   if facts["drone_to_target_m"] is not None else None)
    range_km = _number(drone.get("operational_range_km"))
    if distance_km is not None and range_km is not None:
        facts["range_margin_km"] = range_km - distance_km

    if distance_km is not None and facts["cruise_kmh"]:
        facts["travel_min"] = 60.0 * distance_km / facts["cruise_kmh"]
        airborne = _number(drone.get("time_to_airborne_min")) or 0.0
        facts["eta_min"] = airborne + facts["travel_min"]
        # Out and back, plus a reserve.
        facts["endurance_needed_min"] = (2.0 * facts["travel_min"]
                                         + ENDURANCE_RESERVE_MIN)

    return facts


def feasibility(drone, facts):
    """Return (feasible, [reasons]) for dispatching this drone."""
    reasons = []

    if drone.get("availability") is False:
        reasons.append("marked unavailable")

    status = drone.get("status")
    if status is not None and str(status).strip().lower() not in READY_STATUSES:
        reasons.append(f"status is {status!r}, not a ready status")

    battery = _number(drone.get("battery_percentage"))
    if battery is None:
        reasons.append("battery unknown")
    elif battery < MIN_BATTERY_PERCENT:
        reasons.append(f"battery {battery:.0f}% is below the "
                       f"{MIN_BATTERY_PERCENT:.0f}% minimum")

    if facts["drone_to_target_m"] is None:
        reasons.append("no position for this drone")
    elif facts["range_margin_km"] is None:
        reasons.append("operational range unknown")
    elif facts["range_margin_km"] < 0:
        reasons.append(
            f"target is {facts['drone_to_target_m'] / 1000.0:.1f} km away, "
            f"beyond its {_number(drone.get('operational_range_km')):.1f} km range")

    flight_min = _number(drone.get("max_flight_time_min"))
    if facts["endurance_needed_min"] is None:
        reasons.append("cannot work out flight time for this trip")
    elif flight_min is None:
        reasons.append("max flight time unknown")
    elif flight_min < facts["endurance_needed_min"]:
        reasons.append(
            f"needs about {facts['endurance_needed_min']:.0f} min there and "
            f"back, has {flight_min:.0f} min")

    return (not reasons), reasons


def score_pair(drone, facts):
    """Return (score out of 100, per-factor rows) for a feasible pair."""
    battery = _number(drone.get("battery_percentage"))
    range_km = _number(drone.get("operational_range_km"))

    units = {
        "eta": (1.0 - _clip01(facts["eta_min"] / ETA_CEILING_MIN)
                if facts["eta_min"] is not None else None),
        "range_margin": (_clip01(facts["range_margin_km"] / range_km)
                         if facts["range_margin_km"] is not None and range_km
                         else None),
        "battery": _clip01(battery / 100.0) if battery is not None else None,
        "checkpoint_proximity": (
            1.0 - _clip01(facts["checkpoint_to_target_m"] / CHECKPOINT_CEILING_M)
            if facts["checkpoint_to_target_m"] is not None else None),
        "readiness": (1.0 if drone.get("availability") is not False else 0.0),
    }

    total_weight = sum(WEIGHTS.values())
    earned = 0.0
    rows = []
    for name, weight in WEIGHTS.items():
        unit = units.get(name)
        points = 0.0 if unit is None else weight * unit
        earned += points
        rows.append({
            "name": name,
            "weight": weight,
            "unit": None if unit is None else round(unit, 4),
            "points": round(points, 2),
            "available": unit is not None,
        })

    score = 100.0 * earned / total_weight if total_weight else 0.0
    return round(max(0.0, min(100.0, score)), 1), rows


def describe(checkpoint, drone, facts, score):
    """Plain sentences explaining what this pairing would mean."""
    lines = []
    if facts["eta_min"] is not None:
        airborne = _number(drone.get("time_to_airborne_min")) or 0.0
        lines.append(
            f"Overhead in about {facts['eta_min']:.0f} min "
            f"({airborne:.0f} min to get airborne, "
            f"{facts['travel_min']:.0f} min flying "
            f"{facts['drone_to_target_m'] / 1000.0:.1f} km).")
    if facts["range_margin_km"] is not None:
        lines.append(f"{facts['range_margin_km']:.1f} km of range left over.")
    battery = _number(drone.get("battery_percentage"))
    if battery is not None:
        lines.append(f"Battery {battery:.0f}%.")
    if facts["checkpoint_to_target_m"] is not None:
        lines.append(f"{checkpoint.get('name') or 'Checkpoint'} is "
                     f"{facts['checkpoint_to_target_m'] / 1000.0:.1f} km from "
                     f"the object.")
    if facts["stationed_at_checkpoint"] is False:
        lines.append("Note: this drone is not stationed at this checkpoint.")
    return lines


def _station_assignments(checkpoints, drones):
    """Pair each drone with the checkpoint it actually flies from.

    A drone is only ever launched from where it stands, so pairing one with a
    checkpoint it is not at describes nothing real: it would put the same
    drone in the list several times and make the comparison between the top
    two a drone against itself. Each drone is therefore matched to its
    nearest checkpoint, which is its station; when that is still farther than
    STATIONED_RADIUS_M the pair is kept but flagged, because a drone in the
    field is still worth dispatching.

    Drones with no position, or when there are no checkpoints, yield nothing —
    there is no pairing to describe.
    """
    if not checkpoints or not drones:
        return []

    pairs = []
    for drone in drones:
        drone_lat = _number(drone.get("latitude"))
        drone_lon = _number(drone.get("longitude"))
        if drone_lat is None or drone_lon is None:
            continue

        nearest, nearest_m = None, None
        for checkpoint in checkpoints:
            cp_lat = _number(checkpoint.get("latitude"))
            cp_lon = _number(checkpoint.get("longitude"))
            if cp_lat is None or cp_lon is None:
                continue
            away = haversine_m(drone_lat, drone_lon, cp_lat, cp_lon)
            if nearest_m is None or away < nearest_m:
                nearest, nearest_m = checkpoint, away

        if nearest is not None:
            pairs.append((drone, nearest))
    return pairs


def rank_pairs(checkpoints, drones, target_lat, target_lon):
    """Score every checkpoint-drone pair for a target.

    Returns {"recommended": <best or None>, "options": [...],
             "excluded": [...]}, options best first. Excluded pairs carry
    their reasons so the recommendation can be justified against them.
    """
    options, excluded = [], []

    for drone, checkpoint in _station_assignments(checkpoints, drones):
        facts = evaluate_pair(checkpoint, drone, target_lat, target_lon)
        ok, reasons = feasibility(drone, facts)

        entry = {
            "checkpoint_id": checkpoint.get("id"),
            "checkpoint_name": checkpoint.get("name"),
            "checkpoint_type": checkpoint.get("checkpoint_type"),
            "checkpoint_status": checkpoint.get("status"),
            "drone_id": drone.get("drone_id"),
            "drone_name": drone.get("drone_name"),
            "type_of_drone": drone.get("type_of_drone"),
            "drone_status": drone.get("status"),
            "battery_percentage": _number(drone.get("battery_percentage")),
            "eta_min": (round(facts["eta_min"], 1)
                        if facts["eta_min"] is not None else None),
            "drone_to_target_km": (
                round(facts["drone_to_target_m"] / 1000.0, 2)
                if facts["drone_to_target_m"] is not None else None),
            "checkpoint_to_target_km": (
                round(facts["checkpoint_to_target_m"] / 1000.0, 2)
                if facts["checkpoint_to_target_m"] is not None else None),
            "range_margin_km": (round(facts["range_margin_km"], 2)
                                if facts["range_margin_km"] is not None
                                else None),
            "stationed_at_checkpoint": facts["stationed_at_checkpoint"],
        }

        if not ok:
            entry["feasible"] = False
            entry["reasons"] = reasons
            excluded.append(entry)
            continue

        score, rows = score_pair(drone, facts)
        entry.update({
            "feasible": True,
            "score": score,
            "factors": rows,
            "why": describe(checkpoint, drone, facts, score),
        })
        options.append(entry)

    # Best score first; a sooner ETA breaks a tie, since that is what the
    # decision usually turns on.
    options.sort(key=lambda e: (-e["score"],
                                e["eta_min"] if e["eta_min"] is not None
                                else float("inf")))

    recommended = options[0] if options else None
    if recommended and len(options) > 1:
        runner_up = options[1]
        recommended["why_best"] = _why_best(recommended, runner_up)
    elif recommended:
        recommended["why_best"] = ["The only feasible pairing."]

    return {"recommended": recommended, "options": options,
            "excluded": excluded}


def _why_best(best, runner_up):
    """Say what the top pairing has over the next one."""
    lines = []
    gap = best["score"] - runner_up["score"]
    lines.append(
        f"Scores {best['score']:.0f} against "
        f"{runner_up['score']:.0f} for {runner_up['drone_name']} at "
        f"{runner_up['checkpoint_name']} ({gap:+.0f}).")

    if (best["eta_min"] is not None and runner_up["eta_min"] is not None
            and best["eta_min"] < runner_up["eta_min"]):
        lines.append(f"Overhead {runner_up['eta_min'] - best['eta_min']:.0f} "
                     f"min sooner.")
    if (best["battery_percentage"] is not None
            and runner_up["battery_percentage"] is not None
            and best["battery_percentage"] > runner_up["battery_percentage"]):
        lines.append(f"{best['battery_percentage'] - runner_up['battery_percentage']:.0f}"
                     f"% more battery.")
    if (best["range_margin_km"] is not None
            and runner_up["range_margin_km"] is not None
            and best["range_margin_km"] > runner_up["range_margin_km"]):
        lines.append(f"{best['range_margin_km'] - runner_up['range_margin_km']:.1f}"
                     f" km more range in hand.")
    return lines

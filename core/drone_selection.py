"""Pick the most suitable response drone for a detected object.

Two stages, kept deliberately apart:

  1. Feasibility — a hard filter. A drone that cannot reach the object, is
     unavailable, is not in a ready status, or has too little battery or
     endurance for the round trip is EXCLUDED with a stated reason, never
     merely ranked low: a low rank still reads as "possible".
  2. Score — the survivors, out of 100, over weighted factors.

Every excluded drone keeps its reason, so the choice can be justified against
what was ruled out rather than only announced.

Pure Python: no database, no Qt, no HTTP. The query lives in core/drone_db.py
so the model can be exercised on its own and the weights argued about without
a Postgres to hand.
"""

import math

# --- feasibility -----------------------------------------------------------
# Statuses a drone may be dispatched from. Anything else is excluded by name,
# so an unexpected status is reported rather than silently treated as ready.
READY_STATUSES = ("idle", "ready", "available", "standby", "armed")

MIN_BATTERY_PERCENT = 20.0

# Flight time must cover out and back with this much left over.
ENDURANCE_RESERVE_MIN = 5.0

# --- scoring ---------------------------------------------------------------
# These need not total 100; the score is scaled by whatever they sum to, so
# raising one does not silently shrink the others.
WEIGHTS = {
    "eta": 40.0,            # how soon it can be overhead — usually decisive
    "range_margin": 20.0,   # reach left over beyond the distance flown
    "battery": 25.0,        # charge in hand
    "endurance": 15.0,      # flight time left after the round trip
}

ETA_CEILING_MIN = 60.0          # an ETA at or beyond this scores 0

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
    """Return a real float, else None. Decimal from psycopg converts here."""
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
    """Infer cruise speed from the columns the table actually has.

    There is no speed column, but operational_range_km flown within
    max_flight_time_min implies one. The range is read as one-way, which is
    the conservative choice: if it is meant as a round trip the true speed is
    higher and the ETA below is pessimistic, never optimistic.
    """
    range_km = _number(drone.get("operational_range_km"))
    flight_min = _number(drone.get("max_flight_time_min"))
    if not range_km or not flight_min or range_km <= 0 or flight_min <= 0:
        return None
    return range_km / (flight_min / 60.0)


def evaluate(drone, target_lat, target_lon):
    """Return the facts about sending this drone to the target.

    Distances in metres, times in minutes. Anything that cannot be derived is
    None, and feasibility treats that as disqualifying rather than assuming
    the best case.
    """
    facts = {
        "distance_m": _number(drone.get("distance_m")),
        "cruise_kmh": cruise_speed_kmh(drone),
        "travel_min": None,
        "eta_min": None,
        "range_margin_km": None,
        "endurance_needed_min": None,
        "endurance_spare_min": None,
    }

    # Prefer a distance the database already worked out; fall back to the
    # drone's own coordinates.
    if facts["distance_m"] is None:
        lat = _number(drone.get("latitude"))
        lon = _number(drone.get("longitude"))
        if lat is not None and lon is not None:
            facts["distance_m"] = haversine_m(lat, lon, target_lat, target_lon)

    distance_km = (facts["distance_m"] / 1000.0
                   if facts["distance_m"] is not None else None)
    range_km = _number(drone.get("operational_range_km"))
    if distance_km is not None and range_km is not None:
        facts["range_margin_km"] = range_km - distance_km

    if distance_km is not None and facts["cruise_kmh"]:
        facts["travel_min"] = 60.0 * distance_km / facts["cruise_kmh"]
        airborne = _number(drone.get("time_to_airborne_min")) or 0.0
        facts["eta_min"] = airborne + facts["travel_min"]
        facts["endurance_needed_min"] = (2.0 * facts["travel_min"]
                                         + ENDURANCE_RESERVE_MIN)
        flight_min = _number(drone.get("max_flight_time_min"))
        if flight_min is not None:
            facts["endurance_spare_min"] = (flight_min
                                            - facts["endurance_needed_min"])

    return facts


def feasibility(drone, facts):
    """Return (feasible, [reasons]) for dispatching this drone."""
    reasons = []

    if drone.get("availability") is False:
        reasons.append("marked unavailable")

    status = drone.get("status")
    if status is not None and str(status).strip().lower() not in READY_STATUSES:
        reasons.append(f"status is '{status}', not a ready status")

    battery = _number(drone.get("battery_percentage"))
    if battery is None:
        reasons.append("battery unknown")
    elif battery < MIN_BATTERY_PERCENT:
        reasons.append(f"battery {battery:.0f}% is below the "
                       f"{MIN_BATTERY_PERCENT:.0f}% minimum")

    if facts["distance_m"] is None:
        reasons.append("no position on record")
    elif facts["range_margin_km"] is None:
        reasons.append("operational range unknown")
    elif facts["range_margin_km"] < 0:
        range_km = _number(drone.get("operational_range_km"))
        reasons.append(f"object is {facts['distance_m'] / 1000.0:.1f} km away, "
                       f"beyond its {range_km:.1f} km range")

    if facts["endurance_needed_min"] is None:
        reasons.append("cannot work out flight time for this trip")
    elif _number(drone.get("max_flight_time_min")) is None:
        reasons.append("max flight time unknown")
    elif facts["endurance_spare_min"] is not None and facts["endurance_spare_min"] < 0:
        reasons.append(
            f"needs about {facts['endurance_needed_min']:.0f} min there and "
            f"back, has {_number(drone.get('max_flight_time_min')):.0f} min")

    return (not reasons), reasons


def score(drone, facts):
    """Return (score out of 100, per-factor rows) for a feasible drone."""
    battery = _number(drone.get("battery_percentage"))
    range_km = _number(drone.get("operational_range_km"))
    flight_min = _number(drone.get("max_flight_time_min"))

    units = {
        "eta": (1.0 - _clip01(facts["eta_min"] / ETA_CEILING_MIN)
                if facts["eta_min"] is not None else None),
        "range_margin": (_clip01(facts["range_margin_km"] / range_km)
                         if facts["range_margin_km"] is not None and range_km
                         else None),
        "battery": _clip01(battery / 100.0) if battery is not None else None,
        "endurance": (_clip01(facts["endurance_spare_min"] / flight_min)
                      if facts["endurance_spare_min"] is not None and flight_min
                      else None),
    }

    total_weight = sum(WEIGHTS.values())
    earned = 0.0
    rows = []
    for name, weight in WEIGHTS.items():
        unit = units.get(name)
        points = 0.0 if unit is None else weight * unit
        earned += points
        rows.append({"name": name, "weight": weight,
                     "unit": None if unit is None else round(unit, 4),
                     "points": round(points, 2),
                     "available": unit is not None})

    value = 100.0 * earned / total_weight if total_weight else 0.0
    return round(max(0.0, min(100.0, value)), 1), rows


def describe(drone, facts):
    """Plain sentences about what sending this drone would mean."""
    lines = []
    if facts["eta_min"] is not None:
        airborne = _number(drone.get("time_to_airborne_min")) or 0.0
        lines.append(f"Overhead in about {facts['eta_min']:.0f} min "
                     f"({airborne:.0f} min to get airborne, "
                     f"{facts['travel_min']:.0f} min flying "
                     f"{facts['distance_m'] / 1000.0:.1f} km).")
    battery = _number(drone.get("battery_percentage"))
    if battery is not None:
        lines.append(f"Battery {battery:.0f}%.")
    if facts["range_margin_km"] is not None:
        lines.append(f"{facts['range_margin_km']:.1f} km of range left over.")
    if facts["endurance_spare_min"] is not None:
        lines.append(f"{facts['endurance_spare_min']:.0f} min of flight time "
                     f"spare after the round trip.")
    return lines


def why_best(best, runner_up):
    """What the chosen drone has over the next one."""
    if runner_up is None:
        return ["The only drone that can respond."]
    lines = [f"Scores {best['score']:.0f} against {runner_up['score']:.0f} "
             f"for {runner_up['drone_name']} "
             f"({best['score'] - runner_up['score']:+.0f})."]
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


def select_response_drone(drones, target_lat, target_lon):
    """Choose the most suitable drone for an object at the given position.

    Returns {"selected": <best or None>, "candidates": [...],
             "excluded": [...]}, candidates best first. ``selected`` is None
    when nothing is feasible, and ``excluded`` then says why for each.
    """
    candidates, excluded = [], []

    for drone in drones or []:
        facts = evaluate(drone, target_lat, target_lon)
        ok, reasons = feasibility(drone, facts)

        entry = {
            "drone_id": drone.get("drone_id"),
            "drone_name": drone.get("drone_name"),
            "type_of_drone": drone.get("type_of_drone"),
            "status": drone.get("status"),
            "availability": drone.get("availability"),
            "battery_percentage": _number(drone.get("battery_percentage")),
            "operational_range_km": _number(drone.get("operational_range_km")),
            "max_flight_time_min": _number(drone.get("max_flight_time_min")),
            "time_to_airborne_min": _number(drone.get("time_to_airborne_min")),
            "latitude": _number(drone.get("latitude")),
            "longitude": _number(drone.get("longitude")),
            "distance_km": (round(facts["distance_m"] / 1000.0, 2)
                            if facts["distance_m"] is not None else None),
            "eta_min": (round(facts["eta_min"], 1)
                        if facts["eta_min"] is not None else None),
            "range_margin_km": (round(facts["range_margin_km"], 2)
                                if facts["range_margin_km"] is not None else None),
            "endurance_spare_min": (round(facts["endurance_spare_min"], 1)
                                    if facts["endurance_spare_min"] is not None
                                    else None),
        }

        if not ok:
            entry["feasible"] = False
            entry["reasons"] = reasons
            excluded.append(entry)
            continue

        value, rows = score(drone, facts)
        entry.update({"feasible": True, "score": value, "factors": rows,
                      "why": describe(drone, facts)})
        candidates.append(entry)

    # Best score first; a sooner ETA breaks a tie, since that is what the
    # decision usually turns on.
    candidates.sort(key=lambda e: (-e["score"],
                                   e["eta_min"] if e["eta_min"] is not None
                                   else float("inf")))

    selected = candidates[0] if candidates else None
    if selected:
        selected["why_best"] = why_best(
            selected, candidates[1] if len(candidates) > 1 else None)

    return {"selected": selected, "candidates": candidates,
            "excluded": excluded}

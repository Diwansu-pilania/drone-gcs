"""Threat scoring for a detection record.

Turns one merged detection — the dict the server stores, with equipment_info
and nearby_checkpoints on it — into an explainable score plus the flat feature
row a model would train on.

Three separate ideas, kept apart on purpose:

  capability  what the equipment can do, from equipment_info alone.
  exposure    what it is in reach of, from the /nearby result.
  certainty   how much the detection itself can be trusted.

``severity`` blends capability and exposure: how bad this is if it is real.
``certainty`` stays its own number rather than being folded in, because "a
tank beside a checkpoint" and "possibly a tank, position estimated" are
different problems and averaging them hides which one you have.
``priority`` = severity x certainty, for ranking a list.

Every weight and ceiling below is a deliberate, tunable choice, not a
measured one — there is no labelled data behind them yet. They are declared
as constants so they can be changed in one place and so a stored score can be
reproduced from the SCORING_VERSION that produced it.

Pure Python: no Qt, no I/O, no config import, so it can be applied to a live
detection or to a directory of saved records.
"""

import math
from datetime import datetime

# Bump when any weight, ceiling or formula below changes, so rows scored by
# different versions are never silently mixed in one dataset.
SCORING_VERSION = "1.0.0"

# --- capability ------------------------------------------------------------
# Each equipment_info field contributes a share of capability, after being
# divided by a ceiling and clipped to 0..1. Ceilings are "about the top of the
# range we expect to see", not hard limits; anything at or above scores 1.0.
CAPABILITY_WEIGHTS = {
    "score": 0.40,          # the equipment database's own rating
    "caliber_mm": 0.25,     # firepower
    "max_range_km": 0.20,   # reach
    "pp_kg": 0.15,          # penetration / payload
}
CAPABILITY_CEILINGS = {
    "score": 10.0,
    "caliber_mm": 155.0,
    "max_range_km": 40.0,
    "pp_kg": 100.0,
}

# --- exposure --------------------------------------------------------------
# How much of the exposure score comes from the nearest checkpoint's closeness
# versus how many are in reach at all. Closeness dominates: one checkpoint
# about to be overrun matters more than five at the edge of the radius.
EXPOSURE_PROXIMITY_WEIGHT = 0.70
EXPOSURE_COUNT_WEIGHT = 0.30
# Checkpoints in range at which the count contribution saturates.
EXPOSURE_COUNT_SATURATION = 5

# --- severity --------------------------------------------------------------
SEVERITY_CAPABILITY_WEIGHT = 0.60
SEVERITY_EXPOSURE_WEIGHT = 0.40

# --- certainty -------------------------------------------------------------
# A position projected from the map's default centre is not a fix, so a
# detection resting on one is trusted less than one synced to real telemetry.
POSITION_SOURCE_TRUST = {
    "mavlink": 1.0,
    "default_center": 0.60,
}
POSITION_SOURCE_TRUST_DEFAULT = 0.50

# Bands are reported alongside the number so a row is readable without
# remembering the thresholds. Upper bound is exclusive, except the last.
SEVERITY_BANDS = (
    (25.0, "LOW"),
    (50.0, "MODERATE"),
    (75.0, "HIGH"),
    (float("inf"), "CRITICAL"),
)

# Fallback frame size when a detection does not carry one, used only to turn a
# bounding box into a fraction of the frame.
DEFAULT_IMAGE_WIDTH = 1920
DEFAULT_IMAGE_HEIGHT = 1080


def _number(value):
    """Return ``value`` as a float, or ``None`` if it is not a real number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result


def clip01(value):
    """Clamp to 0..1."""
    if value is None:
        return 0.0
    if value < 0.0:
        return 0.0
    return 1.0 if value > 1.0 else value


def equipment_capability(equipment_info):
    """Return (capability 0..1, per-field contributions).

    Weights of fields that are missing are dropped and the rest renormalised,
    so a partial equipment_info is not silently scored as a weak one.
    """
    if not isinstance(equipment_info, dict):
        return 0.0, {}

    parts = {}
    used_weight = 0.0
    total = 0.0
    for field, weight in CAPABILITY_WEIGHTS.items():
        value = _number(equipment_info.get(field))
        if value is None:
            continue
        ceiling = CAPABILITY_CEILINGS[field]
        normalised = clip01(value / ceiling) if ceiling else 0.0
        parts[field] = normalised
        total += weight * normalised
        used_weight += weight

    if used_weight <= 0.0:
        return 0.0, parts
    return clip01(total / used_weight), parts


def checkpoint_exposure(nearby_checkpoints):
    """Return (exposure 0..1, facts about the checkpoints in reach).

    Exposure is 0 when the lookup found nothing, and also when it never
    answered — an unknown surrounding is not evidence of a safe one, so the
    facts include ``lookup_status`` for filtering such rows out of a dataset
    rather than trusting the 0.
    """
    facts = {
        "lookup_status": None,
        "checkpoint_count": 0,
        "radius_m": None,
        "nearest_checkpoint_m": None,
        "active_checkpoint_count": 0,
        "proximity": 0.0,
        "count_factor": 0.0,
    }
    if not isinstance(nearby_checkpoints, dict):
        return 0.0, facts

    facts["lookup_status"] = nearby_checkpoints.get("status")
    radius_m = _number(nearby_checkpoints.get("radius_m"))
    facts["radius_m"] = radius_m

    checkpoints = nearby_checkpoints.get("checkpoints")
    if not isinstance(checkpoints, list):
        checkpoints = []

    count = _number(nearby_checkpoints.get("checkpoint_count"))
    facts["checkpoint_count"] = int(count) if count is not None else len(checkpoints)

    distances = []
    active = 0
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            continue
        if str(checkpoint.get("status", "")).lower() == "active":
            active += 1
        for key in ("distance_m", "distance", "dist_m"):
            distance = _number(checkpoint.get(key))
            if distance is not None:
                distances.append(distance)
                break
    facts["active_checkpoint_count"] = active

    if distances:
        facts["nearest_checkpoint_m"] = min(distances)

    if facts["checkpoint_count"] <= 0:
        return 0.0, facts

    # Closeness of the nearest, as a fraction of the radius searched. Without
    # a usable distance, assume mid-radius rather than inventing a worst case.
    if facts["nearest_checkpoint_m"] is not None and radius_m and radius_m > 0:
        facts["proximity"] = clip01(1.0 - facts["nearest_checkpoint_m"] / radius_m)
    else:
        facts["proximity"] = 0.5

    facts["count_factor"] = clip01(
        facts["checkpoint_count"] / float(EXPOSURE_COUNT_SATURATION))

    exposure = (EXPOSURE_PROXIMITY_WEIGHT * facts["proximity"]
                + EXPOSURE_COUNT_WEIGHT * facts["count_factor"])
    return clip01(exposure), facts


def detection_certainty(detection):
    """Return (certainty 0..1, its parts).

    The detector's own confidence, discounted by how much the position it was
    scored at can be trusted.
    """
    confidence = _number(detection.get("confidence"))
    confidence = clip01(confidence) if confidence is not None else 0.0

    source = detection.get("position_source")
    trust = POSITION_SOURCE_TRUST.get(source, POSITION_SOURCE_TRUST_DEFAULT)

    return clip01(confidence * trust), {
        "confidence": confidence,
        "position_source": source,
        "position_trust": trust,
    }


def severity_band(severity):
    """Return the band name for a 0..100 severity."""
    for upper, name in SEVERITY_BANDS:
        if severity < upper:
            return name
    return SEVERITY_BANDS[-1][1]


def score_detection(detection):
    """Score one detection record.

    Returns a dict with the three components, the blended severity, the
    certainty, the priority used for ranking, the band, and the scoring
    version that produced them.
    """
    if not isinstance(detection, dict):
        raise TypeError("detection must be a dict")

    capability, capability_parts = equipment_capability(
        detection.get("equipment_info"))
    exposure, exposure_facts = checkpoint_exposure(
        detection.get("nearby_checkpoints"))
    certainty, certainty_parts = detection_certainty(detection)

    severity = 100.0 * (SEVERITY_CAPABILITY_WEIGHT * capability
                        + SEVERITY_EXPOSURE_WEIGHT * exposure)
    priority = severity * certainty

    return {
        "scoring_version": SCORING_VERSION,
        "capability": round(capability, 6),
        "exposure": round(exposure, 6),
        "certainty": round(certainty, 6),
        "severity": round(severity, 3),
        "priority": round(priority, 3),
        "band": severity_band(severity),
        "capability_parts": capability_parts,
        "exposure_facts": exposure_facts,
        "certainty_parts": certainty_parts,
    }


# --- feature extraction ----------------------------------------------------

def _bbox_features(detection):
    """Size and shape of the detection box, in pixels and frame fractions."""
    out = {"bbox_width_px": None, "bbox_height_px": None,
           "bbox_area_px": None, "bbox_aspect": None,
           "bbox_area_fraction": None}
    bbox = detection.get("bbox")
    if not isinstance(bbox, dict):
        return out

    x1, y1 = _number(bbox.get("x1")), _number(bbox.get("y1"))
    x2, y2 = _number(bbox.get("x2")), _number(bbox.get("y2"))
    if None in (x1, y1, x2, y2):
        return out

    width, height = abs(x2 - x1), abs(y2 - y1)
    out["bbox_width_px"] = width
    out["bbox_height_px"] = height
    out["bbox_area_px"] = width * height
    out["bbox_aspect"] = (width / height) if height else None

    frame_w = _number(detection.get("image_width")) or DEFAULT_IMAGE_WIDTH
    frame_h = _number(detection.get("image_height")) or DEFAULT_IMAGE_HEIGHT
    if frame_w and frame_h:
        out["bbox_area_fraction"] = (width * height) / (frame_w * frame_h)
    return out


def _parse_timestamp(value):
    """Return a datetime from a unix or ISO timestamp, else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return None
    return None


def detection_depth_m(detection):
    """Depth distance to the object, from either place it is carried."""
    depth = detection.get("depth")
    if isinstance(depth, dict):
        value = _number(depth.get("distance_m"))
        if value is not None:
            return value
    return _number(detection.get("distance_m"))


def detection_identity(detection):
    """Stable identity for a detection, matching the record store's key."""
    for field in ("image_file", "timestamp", "id"):
        value = detection.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def extract_features(detection, scored=None):
    """Return one flat row of features + score for a detection record.

    Flat and JSON-scalar only, so it writes straight to CSV or a dataframe.
    ``threat_label`` is left empty for a human judgement; see the note in
    build_threat_dataset.py about why the computed score must not be used as
    the training target on its own.
    """
    scored = scored or score_detection(detection)
    equipment = detection.get("equipment_info")
    equipment = equipment if isinstance(equipment, dict) else {}
    exposure_facts = scored["exposure_facts"]
    nearby = detection.get("nearby_checkpoints")
    nearby = nearby if isinstance(nearby, dict) else {}

    when = _parse_timestamp(detection.get("timestamp"))
    depth_m = detection_depth_m(detection)
    nearest = exposure_facts["nearest_checkpoint_m"]
    max_range_km = _number(equipment.get("max_range_km"))

    row = {
        # identity, for joining back and for splitting train/test by object
        "detection_key": detection_identity(detection),
        "detection_id": detection.get("id"),
        "track_id": detection.get("track_id"),
        "image_file": detection.get("image_file"),
        "datetime": detection.get("datetime"),

        # what was seen
        "object_class": detection.get("object_class") or detection.get("class"),
        "confidence": _number(detection.get("confidence")),
        "depth_distance_m": depth_m,
        "ground_distance_m": _number(detection.get("ground_distance")),

        # how sure we are of where it is
        "has_gps": bool(detection.get("has_gps")),
        "position_source": detection.get("position_source"),
        "position_is_estimated": detection.get("position_source") == "default_center",
        "position_trust": scored["certainty_parts"]["position_trust"],
        "latitude": _number(detection.get("latitude")),
        "longitude": _number(detection.get("longitude")),
        "sync_time_diff_s": _number(detection.get("sync_time_diff")),

        # what it is
        "equipment_name": equipment.get("name"),
        "equipment_category": equipment.get("category"),
        "equipment_domain": equipment.get("domain"),
        "equipment_mobility": equipment.get("mobility_type"),
        "equipment_status": equipment.get("status"),
        "caliber_mm": _number(equipment.get("caliber_mm")),
        "max_range_km": max_range_km,
        "pp_kg": _number(equipment.get("pp_kg")),
        "equipment_score": _number(equipment.get("score")),

        # what it is near
        "checkpoint_lookup_status": exposure_facts["lookup_status"],
        "checkpoint_count": exposure_facts["checkpoint_count"],
        "active_checkpoint_count": exposure_facts["active_checkpoint_count"],
        "search_radius_m": exposure_facts["radius_m"],
        "radius_source": nearby.get("radius_source"),
        "nearest_checkpoint_m": nearest,
        # How far in, as a fraction of the reach that was searched.
        "nearest_checkpoint_frac_of_range": (
            clip01(1.0 - nearest / exposure_facts["radius_m"])
            if nearest is not None and exposure_facts["radius_m"] else None),
        "any_checkpoint_in_range": exposure_facts["checkpoint_count"] > 0,

        # when
        "hour_of_day": when.hour if when else None,
        "is_night": (when.hour < 6 or when.hour >= 18) if when else None,

        # box geometry, a proxy for apparent size
        **_bbox_features(detection),

        # the score's components, so a row explains its own number
        "capability": scored["capability"],
        "exposure": scored["exposure"],
        "certainty": scored["certainty"],
        "threat_severity": scored["severity"],
        "threat_priority": scored["priority"],
        "threat_band": scored["band"],
        "scoring_version": scored["scoring_version"],

        # for a human to fill in; the target a model should actually learn
        "threat_label": "",
    }
    return row


FEATURE_COLUMNS = list(extract_features({
    "bbox": {"x1": 0, "y1": 0, "x2": 1, "y2": 1},
}).keys())

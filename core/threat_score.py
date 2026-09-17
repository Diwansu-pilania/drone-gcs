"""Threat score for a detected object, out of 100.

Five factors, each normalised to 0..1 and multiplied by its weight:

    checkpoints   how many checkpoints lie within the object's reach
    range         equipment_info.max_range_km
    score         equipment_info.score, the equipment database's own rating
    domain        equipment_info.domain, mapped through a table
    pp            equipment_info.pp_kg

Weights, ceilings and the domain table all live in config.py so they can be
tuned without touching this file. The score is scaled by the weights actually
in play, so the result always reads out of 100 even after retuning.

A factor whose value is missing contributes nothing AND keeps its weight in
the denominator: a detection the equipment database does not recognise really
is less of a known threat, and ``factors_present`` reports how many of the
five were available so a low score from thin data can be told apart from a
genuinely low one.

Pure Python, no Qt: usable on a live detection or a stored record.
"""

import math

import config

SCORE_VERSION = "2.0.0"

FACTOR_LABELS = {
    "checkpoints": "Checkpoints in range",
    "range": "Max range",
    "score": "Equipment score",
    "domain": "Domain",
    "pp": "PP",
}


def _number(value):
    """Return ``value`` as a real float, else ``None``."""
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


def _ceiling(name):
    return _number(getattr(config, "THREAT_CEILINGS", {}).get(name)) or 0.0


def domain_weight(domain):
    """Return a domain's 0..1 weight from the configured table."""
    table = getattr(config, "THREAT_DOMAIN_WEIGHTS", {})
    default = _number(getattr(config, "THREAT_DOMAIN_DEFAULT", 0.5)) or 0.0
    if not isinstance(domain, str) or not domain.strip():
        return None
    return _clip01(_number(table.get(domain.strip().lower(), default)))


def checkpoints_in_range(detection):
    """Return how many checkpoints the /nearby lookup found, else ``None``.

    ``None`` means the lookup never answered, which is not the same as a
    confirmed zero and must not be scored as one.
    """
    nearby = detection.get("nearby_checkpoints")
    if not isinstance(nearby, dict):
        return None
    if nearby.get("status") not in (None, "ok"):
        return None

    count = _number(nearby.get("checkpoint_count"))
    if count is not None:
        return max(0.0, count)

    checkpoints = nearby.get("checkpoints")
    return float(len(checkpoints)) if isinstance(checkpoints, list) else None


def factor_values(detection):
    """Return each factor's raw value, or ``None`` where it is unavailable."""
    equipment = detection.get("equipment_info")
    equipment = equipment if isinstance(equipment, dict) else {}

    return {
        "checkpoints": checkpoints_in_range(detection),
        "range": _number(equipment.get("max_range_km")),
        "score": _number(equipment.get("score")),
        "domain": equipment.get("domain"),
        "pp": _number(equipment.get("pp_kg")),
    }


def normalise(name, value):
    """Return a factor's 0..1 contribution, or ``None`` if unavailable."""
    if value is None:
        return None
    if name == "domain":
        return domain_weight(value)
    ceiling = _ceiling(name)
    if ceiling <= 0:
        return None
    return _clip01(value / ceiling)


def band_for(score):
    """Return the band name for a 0..100 score."""
    bands = getattr(config, "THREAT_BANDS", ((0.0, "LOW"),))
    for threshold, name in bands:
        if score >= threshold:
            return name
    return bands[-1][1]


def score_detection(detection):
    """Return this detection's threat score out of 100, with its breakdown.

    The returned dict carries ``score`` (0..100), ``band``, ``factors_present``
    of ``factors_total``, and a ``factors`` list of per-factor rows — raw
    value, normalised 0..1, weight, and the points it contributed — so the
    number can be explained without recomputing it.
    """
    if not isinstance(detection, dict):
        raise TypeError("detection must be a dict")

    weights = getattr(config, "THREAT_WEIGHTS", {})
    total_weight = sum(w for w in (_number(v) for v in weights.values())
                       if w and w > 0)

    values = factor_values(detection)
    rows = []
    earned = 0.0
    present = 0

    for name, weight in weights.items():
        weight = _number(weight) or 0.0
        if weight <= 0:
            continue
        raw = values.get(name)
        unit = normalise(name, raw)
        if unit is None:
            points = 0.0
        else:
            points = weight * unit
            earned += points
            present += 1
        rows.append({
            "name": name,
            "label": FACTOR_LABELS.get(name, name),
            "raw": raw,
            "unit": None if unit is None else round(unit, 6),
            "weight": weight,
            "points": round(points, 3),
            "available": unit is not None,
        })

    # Scaled by the weights in play, so the result reads out of 100 whatever
    # the weights were retuned to.
    score = (100.0 * earned / total_weight) if total_weight > 0 else 0.0
    score = max(0.0, min(100.0, score))

    # Nothing to score on is not the same as nothing to worry about: calling
    # it LOW would read as a judgement the data does not support.
    band = band_for(score) if present else "UNSCORED"

    return {
        "version": SCORE_VERSION,
        "score": round(score, 1),
        "band": band,
        "factors_present": present,
        "factors_total": len(rows),
        "factors": rows,
    }

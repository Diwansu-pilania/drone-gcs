"""Client for the external nearby-checkpoint service.

Once a detection has a projected object position, the GCS asks the service
which checkpoints lie within the detected equipment's reach:

    GET <base>/nearby?latitude=..&longitude=..&radius_m=..

    -> {"center": {"latitude": .., "longitude": ..},
        "radius_m": ..,
        "checkpoint_count": N,
        "checkpoints": [...]}

Pure HTTP + parsing, with no Qt or UI dependency, so it can be called from a
worker thread and exercised on its own.
"""

import requests

# Detector payloads carry the equipment's reach in kilometres; the service
# takes metres.
METRES_PER_KM = 1000.0


def equipment_max_range_km(detection):
    """Return the ``max_range_km`` from a detection's equipment info.

    The detector nests it under ``equipment_info``:

        "equipment_info": {"name": "military_tank", "max_range_km": 5, ...}

    Returns ``None`` when the field is absent or not a positive number, which
    is what makes the caller fall back to the configured default radius.
    """
    info = detection.get("equipment_info")
    if not isinstance(info, dict):
        return None

    value = info.get("max_range_km")
    try:
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

    return value if value is not None and value > 0 else None


def radius_from_equipment(detection, default_radius_m):
    """Return the /nearby search radius in metres for ``detection``.

    Uses the equipment's maximum range converted from km to m, falling back to
    ``default_radius_m`` when the detection carries no usable value.
    """
    range_km = equipment_max_range_km(detection)
    if range_km is None:
        return float(default_radius_m)
    return range_km * METRES_PER_KM


def fetch_nearby_checkpoints(api_base, latitude, longitude, radius_m,
                             timeout=5.0):
    """Query the checkpoint service and return its parsed response.

    Args:
        api_base: Service root, e.g. "http://100.101.102.103:8000". An empty
            value means the lookup is switched off.
        latitude, longitude: Centre of the search — the projected object
            position, not the drone's.
        radius_m: Search radius in metres; the service rejects values <= 0.
        timeout: Seconds to wait for the response.

    Returns:
        The service's response dict, or ``None`` when the lookup is disabled,
        the arguments are unusable, or the request/parse fails. Callers treat
        ``None`` as "no checkpoints this time" — a missing peer must never
        break detection handling.
    """
    if not api_base:
        return None
    if latitude is None or longitude is None:
        return None

    try:
        radius_m = float(radius_m)
    except (TypeError, ValueError):
        return None
    if radius_m <= 0:
        return None

    url = f"{api_base.rstrip('/')}/nearby"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "radius_m": radius_m,
    }

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[checkpoints] {url} failed: {exc}")
        return None

    if not isinstance(payload, dict):
        print(f"[checkpoints] {url} returned {type(payload).__name__}, "
              f"expected an object")
        return None

    return payload


def checkpoints_from_response(payload):
    """Return the checkpoint list from a service response.

    Tolerates a response whose ``checkpoints`` key is missing or not a list, so
    a partial reply degrades to "no checkpoints" instead of raising.
    """
    if not isinstance(payload, dict):
        return []
    checkpoints = payload.get("checkpoints")
    return checkpoints if isinstance(checkpoints, list) else []

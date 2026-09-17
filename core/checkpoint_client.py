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

import json
from urllib.parse import urlencode

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
                             timeout=5.0, error_out=None):
    """Query the checkpoint service and return its parsed response.

    Args:
        api_base: Service root, e.g. "http://100.101.102.103:8000". An empty
            value means the lookup is switched off.
        latitude, longitude: Centre of the search — the projected object
            position, not the drone's.
        radius_m: Search radius in metres; the service rejects values <= 0.
        timeout: Seconds to wait for the response.
        error_out: Optional dict filled in on failure with ``kind`` and
            ``message``. A peer that answers too slowly is a different
            problem from one that is not there, and the UI says which.

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

    # Logged before sending so an unanswered request is still visible: without
    # this a wrong address looks identical to the lookup never firing.
    print(f"[checkpoints] GET {url}?{urlencode(params)}")

    def record(kind, message):
        if error_out is not None:
            error_out["kind"] = kind
            error_out["message"] = message
        print(f"[checkpoints] {message}")

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        # The peer accepted the connection and did not answer in time, which
        # is a slow service rather than a wrong address.
        record("timeout",
               f"{url} timed out after {timeout:g}s - the service is "
               f"reachable but did not answer. Raise "
               f"CHECKPOINT_API_TIMEOUT, or speed the query up.")
        return None
    except requests.ConnectionError as exc:
        record("unreachable",
               f"{url} could not be reached: {exc}. Check the address, the "
               f"port, and that the service is running.")
        return None
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", "?")
        record("http", f"{url} answered HTTP {status}: {exc}")
        return None
    except ValueError as exc:
        record("bad_json", f"{url} did not return JSON: {exc}")
        return None
    except requests.RequestException as exc:
        record("error", f"{url} failed: {exc}")
        return None

    if not isinstance(payload, dict):
        record("bad_json", f"{url} returned {type(payload).__name__}, "
                           f"expected an object")
        return None

    # The raw reply, trimmed. The service's field names are not fixed by
    # anything on this side, so when checkpoints come back but nothing
    # appears, this is what says why.
    body = json.dumps(payload)
    print(f"[checkpoints] <- {body[:600]}{'...' if len(body) > 600 else ''}")

    return payload


def checkpoint_position(checkpoint):
    """Return a checkpoint's (lat, lon), or ``(None, None)``.

    The service names these fields as it likes, so accept the usual spellings
    and the common nested/pair shapes rather than silently plotting nothing.
    """
    if not isinstance(checkpoint, dict):
        # A bare [lat, lon] pair.
        if isinstance(checkpoint, (list, tuple)) and len(checkpoint) >= 2:
            try:
                return float(checkpoint[0]), float(checkpoint[1])
            except (TypeError, ValueError):
                return None, None
        return None, None

    for lat_key, lon_key in (("latitude", "longitude"), ("lat", "lon"),
                             ("lat", "lng"), ("y", "x")):
        if lat_key in checkpoint and lon_key in checkpoint:
            try:
                return float(checkpoint[lat_key]), float(checkpoint[lon_key])
            except (TypeError, ValueError):
                return None, None

    # A nested position object or pair.
    for key in ("location", "position", "coords", "coordinates", "point"):
        nested = checkpoint.get(key)
        if nested is not None and nested is not checkpoint:
            found = checkpoint_position(nested)
            if found != (None, None):
                return found

    # Last resort: any field whose NAME says latitude/longitude, so a spelling
    # not listed above (checkpoint_lat, latitude_deg, gps_lon, ...) still
    # plots instead of being silently dropped.
    lat_key = _first_key_matching(checkpoint, ("lat",))
    lon_key = _first_key_matching(checkpoint, ("lon", "lng"))
    if lat_key and lon_key:
        try:
            return float(checkpoint[lat_key]), float(checkpoint[lon_key])
        except (TypeError, ValueError):
            return None, None

    return None, None


def _first_key_matching(mapping, fragments):
    """Return the first key containing one of ``fragments``, with a number."""
    for key, value in mapping.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if not any(fragment in lowered for fragment in fragments):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            float(value)
        except (TypeError, ValueError):
            continue
        return key
    return None


def checkpoint_name(checkpoint):
    """Return something printable to identify a checkpoint."""
    if not isinstance(checkpoint, dict):
        return "Checkpoint"
    for key in ("name", "title", "checkpoint_name", "label", "id"):
        value = checkpoint.get(key)
        if value is not None and value != "":
            return str(value)

    # A spelling not listed above (site_name, cp_label, ...) still beats
    # showing every checkpoint as "Checkpoint".
    for key, value in checkpoint.items():
        if not isinstance(key, str) or value in (None, ""):
            continue
        lowered = key.lower()
        if any(f in lowered for f in ("name", "title", "label")):
            if isinstance(value, (str, int, float)):
                return str(value)
    return "Checkpoint"


def checkpoint_distance_m(checkpoint):
    """Return a checkpoint's reported distance in metres, else ``None``."""
    if not isinstance(checkpoint, dict):
        return None
    for key in ("distance_m", "distance", "distance_meters", "dist_m"):
        value = checkpoint.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    for key in ("distance_km", "dist_km"):
        value = checkpoint.get(key)
        if value is not None:
            try:
                return float(value) * METRES_PER_KM
            except (TypeError, ValueError):
                return None
    return None


def checkpoints_from_response(payload):
    """Return the checkpoint list from a service response.

    Tolerates a response whose ``checkpoints`` key is missing or not a list, so
    a partial reply degrades to "no checkpoints" instead of raising.
    """
    if not isinstance(payload, dict):
        return []
    checkpoints = payload.get("checkpoints")
    if isinstance(checkpoints, list):
        return checkpoints
    # Some services key the set by id instead of listing it.
    if isinstance(checkpoints, dict):
        return list(checkpoints.values())
    return []

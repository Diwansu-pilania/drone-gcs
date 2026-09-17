"""Client for the response-recommendation service on the database machine.

The GCS holds no database credentials. It posts a detected object and its
threat score to the peer, which ranks checkpoint-drone pairings and answers
with the best one and the reasoning; the GCS then shows that to an operator,
who authorises or rejects it.

    POST <base>/response/recommend   -> ranked pairings
    POST <base>/response/authorize   -> record a person's decision

HTTP and parsing only, no Qt, so it runs on a worker thread and can be tested
on its own. Every failure returns None with a reason recorded, never an
exception: a peer that is down must not break detection handling, and it must
never look like "no response available".
"""

import json

import requests


def request_recommendation(api_base, latitude, longitude, radius_m,
                           threat_score=None, threat_band=None,
                           object_class=None, detection_ref=None,
                           drone_search_radius_m=None, timeout=30.0,
                           error_out=None):
    """Ask the peer which checkpoint-drone pairing should respond.

    Args:
        api_base: Service root. Empty means the lookup is switched off.
        latitude, longitude: The projected object position, not the drone's.
        radius_m: Checkpoint search radius, normally the equipment's max range.
        threat_score, threat_band, object_class: Carried through for context
            and stored with any authorisation.
        detection_ref: Identifies the detection, e.g. its image_file.
        drone_search_radius_m: How far out to consider drones; the service
            picks a default when this is None.
        error_out: Optional dict filled in on failure with ``kind`` and
            ``message``.

    Returns the response dict, or None.
    """
    if not api_base or latitude is None or longitude is None:
        return None
    try:
        radius_m = float(radius_m)
    except (TypeError, ValueError):
        return None
    if radius_m <= 0:
        return None

    url = f"{api_base.rstrip('/')}/response/recommend"
    body = {
        "latitude": latitude,
        "longitude": longitude,
        "radius_m": radius_m,
        "threat_score": threat_score,
        "threat_band": threat_band,
        "object_class": object_class,
        "detection_ref": detection_ref,
    }
    if drone_search_radius_m:
        body["drone_search_radius_m"] = float(drone_search_radius_m)

    return _post(url, body, timeout, error_out, label="recommendation")


def authorize_response(api_base, decision, authorized_by, checkpoint_id=None,
                       drone_id=None, detection_ref=None, threat_score=None,
                       recommendation=None, note=None, timeout=30.0,
                       error_out=None):
    """Record an operator's approval or rejection of a recommended pairing.

    This records a decision. It does not dispatch anything: acting on an
    approval is a separate, deliberate step on the service side.
    """
    if not api_base:
        return None
    decision = str(decision).strip().lower()
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not authorized_by:
        raise ValueError("authorized_by is required: a decision needs an owner")

    url = f"{api_base.rstrip('/')}/response/authorize"
    body = {
        "decision": decision,
        "authorized_by": authorized_by,
        "checkpoint_id": checkpoint_id,
        "drone_id": drone_id,
        "detection_ref": detection_ref,
        "threat_score": threat_score,
        "recommendation": recommendation,
        "note": note,
    }
    return _post(url, body, timeout, error_out, label="authorization")


def _post(url, body, timeout, error_out, label):
    """POST JSON and return the parsed object, or None with a reason."""
    def record(kind, message):
        if error_out is not None:
            error_out["kind"] = kind
            error_out["message"] = message
        print(f"[response] {message}")

    print(f"[response] POST {url} ({label})")

    try:
        reply = requests.post(url, json=body, timeout=timeout)
        reply.raise_for_status()
        payload = reply.json()
    except requests.Timeout:
        record("timeout", f"{url} timed out after {timeout:g}s - the service "
                          f"is reachable but did not answer.")
        return None
    except requests.ConnectionError as exc:
        record("unreachable", f"{url} could not be reached: {exc}")
        return None
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", "?")
        detail = ""
        try:
            detail = f" — {exc.response.json().get('detail', '')}"
        except Exception:
            pass
        record("http", f"{url} answered HTTP {status}{detail}")
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

    text = json.dumps(payload)
    print(f"[response] <- {text[:500]}{'...' if len(text) > 500 else ''}")
    return payload


def recommended_pairing(payload):
    """Return the recommended pairing from a response, or None."""
    if not isinstance(payload, dict):
        return None
    best = payload.get("recommended")
    return best if isinstance(best, dict) else None


def pairing_summary(pairing):
    """One line naming the drone and where it flies from."""
    if not isinstance(pairing, dict):
        return ""
    drone = pairing.get("drone_name") or f"drone {pairing.get('drone_id')}"
    checkpoint = (pairing.get("checkpoint_name")
                  or f"checkpoint {pairing.get('checkpoint_id')}")
    return f"{drone} from {checkpoint}"

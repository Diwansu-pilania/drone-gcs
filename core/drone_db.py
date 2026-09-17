"""Read candidate response drones from the shooter_drones table.

The connection string is read from the DRONE_DATABASE_URL environment
variable, or DATABASE_URL, or a .env file beside the project. It is NEVER
written into the source or into config.py: those are committed, and a
database credential in a repository is a credential published to everyone
who can read it.

Only the query lives here. Choosing between the rows it returns is
core/drone_selection.py, which needs no database and can be tested alone.
"""

import os
import threading

DEFAULT_SEARCH_RADIUS_M = 100000.0     # 100 km; scoring rejects what is too far
CONNECT_TIMEOUT_S = 15

# Loaded once, so a missing driver is reported clearly rather than raising on
# a worker thread at the worst moment.
_psycopg = None
_psycopg_error = None
_load_lock = threading.Lock()


def _driver():
    """Return the psycopg module, or None with the reason recorded."""
    global _psycopg, _psycopg_error
    with _load_lock:
        if _psycopg is None and _psycopg_error is None:
            try:
                import psycopg
                _psycopg = psycopg
            except ImportError as exc:
                _psycopg_error = (f"psycopg is not installed ({exc}). "
                                  f"pip install 'psycopg[binary]'")
    return _psycopg


def database_url():
    """Return the configured connection string, or None.

    Checked in order: DRONE_DATABASE_URL, DATABASE_URL, then a .env file in
    the project root. python-dotenv is used when present; a small parser
    handles the file otherwise, so the feature does not need an extra
    dependency just to read one line.
    """
    for name in ("DRONE_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(name)
        if value:
            return value.strip()

    env_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return None

    try:
        from dotenv import dotenv_values
        values = dotenv_values(env_path)
    except ImportError:
        values = {}
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            return None

    for name in ("DRONE_DATABASE_URL", "DATABASE_URL"):
        if values.get(name):
            return values[name].strip()
    return None


def redact(url):
    """Return a connection string safe to print: password removed.

    Connection strings reach logs and screenshots, so nothing here prints one
    without going through this first.
    """
    if not url:
        return ""
    try:
        head, _, tail = url.partition("://")
        if "@" not in tail:
            return url
        credentials, _, host = tail.partition("@")
        user, _, password = credentials.partition(":")
        return f"{head}://{user}:{'***' if password else ''}@{host}"
    except Exception:
        return "<connection string>"


# One query. Distance is computed in the database, which already has the
# index, and latitude/longitude come back for the map and for a fallback
# distance if the geography column is ever empty.
DRONE_QUERY = """
    SELECT
        drone_id,
        drone_name,
        type_of_drone,
        status,
        availability,
        battery_percentage,
        max_flight_time_min,
        operational_range_km,
        time_to_airborne_min,
        ST_Y(cur_location::geometry) AS latitude,
        ST_X(cur_location::geometry) AS longitude,
        ST_Distance(
            cur_location,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
        ) AS distance_m
    FROM shooter_drones
    WHERE cur_location IS NOT NULL
      AND ST_DWithin(
            cur_location,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
      )
    ORDER BY distance_m
    LIMIT %s;
"""

COLUMNS = ("drone_id", "drone_name", "type_of_drone", "status", "availability",
           "battery_percentage", "max_flight_time_min", "operational_range_km",
           "time_to_airborne_min", "latitude", "longitude", "distance_m")


def fetch_candidate_drones(latitude, longitude, radius_m=None, limit=50,
                           url=None, error_out=None):
    """Return drones near a point, nearest first, as plain dicts.

    Every drone with a position inside the radius is returned, whatever its
    status or battery: rejecting them here would lose the reason, and
    drone_selection reports each exclusion by name.

    Returns [] and records a reason in ``error_out`` on any failure — a
    database that is down must never break detection handling.
    """
    def record(kind, message):
        if error_out is not None:
            error_out["kind"] = kind
            error_out["message"] = message
        print(f"[drones] {message}")

    psycopg = _driver()
    if psycopg is None:
        record("no_driver", _psycopg_error or "psycopg unavailable")
        return []

    url = url or database_url()
    if not url:
        record("no_url", "no DRONE_DATABASE_URL or DATABASE_URL set, and no "
                         ".env in the project root — drone selection is off")
        return []

    if latitude is None or longitude is None:
        record("no_position", "no object position, so no drones were looked up")
        return []

    radius_m = float(radius_m or DEFAULT_SEARCH_RADIUS_M)
    print(f"[drones] querying shooter_drones within {radius_m / 1000.0:.0f} km "
          f"of {latitude:.6f}, {longitude:.6f} via {redact(url)}")

    try:
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S) as conn:
            with conn.cursor() as cur:
                cur.execute(DRONE_QUERY, (longitude, latitude,
                                          longitude, latitude,
                                          radius_m, limit))
                rows = cur.fetchall()
    except psycopg.OperationalError as exc:
        record("unreachable", f"could not reach the database: "
                              f"{str(exc).strip()[:200]}")
        return []
    except psycopg.Error as exc:
        # An undefined table or column lands here, and the message names it.
        record("query_failed", f"shooter_drones query failed: "
                               f"{str(exc).strip()[:200]}")
        return []
    except Exception as exc:
        record("error", f"unexpected failure reading shooter_drones: {exc}")
        return []

    drones = [dict(zip(COLUMNS, row)) for row in rows]
    print(f"[drones] {len(drones)} drone(s) within range")
    return drones

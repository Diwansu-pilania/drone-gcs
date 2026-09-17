"""Position synchronization - match detection timestamps to drone positions"""
from datetime import datetime, timedelta


def find_drone_position_at_time(flight_path, detection_timestamp, max_time_diff_sec=30):
    """Find the drone's position closest to the detection timestamp.

    Args:
        flight_path: List of (lat, lon, datetime) tuples from VehicleState
        detection_timestamp: ISO string or Unix timestamp from detection
        max_time_diff_sec: Maximum allowed time difference in seconds

    Returns:
        (lat, lon, time_diff_sec) if found, or (None, None, None) if no match
    """
    if not flight_path:
        return None, None, None

    # Parse detection timestamp
    det_time = _parse_timestamp(detection_timestamp)
    if det_time is None:
        return None, None, None

    # Find closest position in flight path
    closest_pos = None
    min_diff = float('inf')

    for lat, lon, pos_time in flight_path:
        time_diff = abs((det_time - pos_time).total_seconds())
        if time_diff < min_diff:
            min_diff = time_diff
            closest_pos = (lat, lon, time_diff)

    # Return only if within acceptable time window
    if closest_pos and closest_pos[2] <= max_time_diff_sec:
        return closest_pos

    return None, None, None


def _parse_timestamp(timestamp):
    """Parse various timestamp formats to datetime object."""
    if isinstance(timestamp, datetime):
        return timestamp

    if isinstance(timestamp, (int, float)):
        # Unix timestamp
        return datetime.fromtimestamp(timestamp)

    if isinstance(timestamp, str):
        try:
            # ISO format: "2026-08-10T13:56:53" or "2026-08-10 13:56:53"
            return datetime.fromisoformat(timestamp.replace(' ', 'T'))
        except ValueError:
            pass

    return None

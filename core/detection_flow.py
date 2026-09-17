"""Two-stage detection handling shared by the API merge and Qt UI."""

from core.detection_projection import project_detection_position
from core.position_sync import find_drone_position_at_time


def positive_distance(detection):
    """Return a positive depth distance as float, otherwise ``None``."""
    depth = detection.get("depth")
    value = depth.get("distance_m") if isinstance(depth, dict) else None
    if value is None:
        value = detection.get("distance_m")
    try:
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value > 0 else None


def should_replace_depth(current_depth, incoming_depth):
    """Prevent a processing retry from replacing a completed depth result."""
    current_status = (current_depth or {}).get("status")
    incoming_status = (incoming_depth or {}).get("status")
    return not (current_status == "complete" and incoming_status == "processing")


def prepare_detection_for_map(detection, vehicle_state, settings):
    """Enrich a detection update and report whether it is ready for the map.

    The initial detector POST has no distance. With projection enabled it must
    remain off-map until the completed depth POST arrives; otherwise the target
    is incorrectly shown at the drone position as a zero-distance detection.
    """
    sender_has_gps = bool(detection.get("has_gps"))
    depth = detection.get("depth")
    depth_status = depth.get("status") if isinstance(depth, dict) else None
    distance_m = positive_distance(detection)

    drone_lat = drone_lon = time_diff = None
    if distance_m is not None or not settings.ENABLE_PROJECTION:
        drone_lat, drone_lon, time_diff = find_drone_position_at_time(
            vehicle_state.flight_path,
            detection.get("timestamp"),
            max_time_diff_sec=settings.MAX_SYNC_TIME_DIFF,
        )

    if drone_lat is not None and drone_lon is not None:
        if settings.ENABLE_PROJECTION and distance_m is not None:
            object_lat, object_lon, ground_dist = project_detection_position(
                drone_lat=drone_lat,
                drone_lon=drone_lon,
                drone_alt=vehicle_state.altitude,
                roll=vehicle_state.roll,
                pitch=vehicle_state.pitch,
                heading=vehicle_state.heading,
                distance_m=distance_m,
                bbox=detection.get("bbox"),
                image_width=detection.get("image_width", settings.IMAGE_WIDTH),
                image_height=detection.get("image_height", settings.IMAGE_HEIGHT),
                camera_fov_horizontal=settings.CAMERA_FOV_HORIZONTAL,
                camera_fov_vertical=settings.CAMERA_FOV_VERTICAL,
                camera_pitch_offset=settings.CAMERA_PITCH_OFFSET,
            )

            if object_lat is not None and object_lon is not None:
                detection.update({
                    "latitude": object_lat,
                    "longitude": object_lon,
                    "has_gps": True,
                    "altitude": 0.0,
                    "sync_time_diff": time_diff,
                    "ground_distance": ground_dist,
                    "drone_lat": drone_lat,
                    "drone_lon": drone_lon,
                    "drone_alt": vehicle_state.altitude,
                })
                if settings.DEBUG_PROJECTION:
                    print(
                        f"[Projection] {detection.get('class')} @ "
                        f"drone({drone_lat:.6f},{drone_lon:.6f}) -> "
                        f"object({object_lat:.6f},{object_lon:.6f}), "
                        f"dist={distance_m:.1f}m, ground={ground_dist:.1f}m"
                    )
            else:
                detection["has_gps"] = sender_has_gps
        elif not settings.ENABLE_PROJECTION and not sender_has_gps:
            detection.update({
                "latitude": drone_lat,
                "longitude": drone_lon,
                "has_gps": True,
                "altitude": vehicle_state.altitude,
                "sync_time_diff": time_diff,
            })

    return {
        "should_map": bool(detection.get("has_gps")),
        "waiting_for_depth": (
            settings.ENABLE_PROJECTION
            and not sender_has_gps
            and distance_m is None
            and depth_status != "error"
        ),
        "depth_error": depth_status == "error",
    }

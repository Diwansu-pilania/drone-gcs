"""Calculate actual detection position using drone pose and depth.

Given:
  - Drone position (lat, lon, altitude)
  - Drone attitude (roll, pitch, yaw/heading)
  - Distance to detected object
  - Object's position in image (bbox center)
  - Camera FOV

Calculate:
  - Object's actual GPS position (lat, lon)
"""
import math


def project_detection_position(
    drone_lat, drone_lon, drone_alt,
    roll, pitch, heading,
    distance_m,
    bbox,
    image_width, image_height,
    camera_fov_horizontal, camera_fov_vertical,
    camera_pitch_offset,
):
    """
    Project detection from drone to ground position.

    Args:
        drone_lat, drone_lon: Drone GPS position (degrees)
        drone_alt: Drone altitude MSL (meters)
        roll, pitch, heading: Drone attitude (radians for roll/pitch, degrees for heading)
        distance_m: Distance to object from depth estimation (meters)
        bbox: Detection bounding box dict with x1, y1, x2, y2 (pixels)
        image_width, image_height: Image dimensions (pixels)
        camera_fov_horizontal, camera_fov_vertical: Camera field of view (degrees)
        camera_pitch_offset: Camera tilt angle (degrees, positive = down)

    Returns:
        (object_lat, object_lon, ground_distance_m) or (None, None, None) if invalid
    """
    # No depth, no projection.
    if distance_m is None or distance_m <= 0:
        return None, None, None

    # Where the object sits in the frame; fall back to the frame centre.
    if bbox and all(k in bbox for k in ('x1', 'y1', 'x2', 'y2')):
        bbox_center_x = (bbox['x1'] + bbox['x2']) / 2.0
        bbox_center_y = (bbox['y1'] + bbox['y2']) / 2.0
    else:
        bbox_center_x = image_width / 2.0
        bbox_center_y = image_height / 2.0

    # Offset from the image centre, as a fraction of the frame
    # (-0.5 .. +0.5).
    norm_x = bbox_center_x / image_width - 0.5
    norm_y = bbox_center_y / image_height - 0.5

    # Convert that offset into angles off the camera axis.
    angle_x = norm_x * camera_fov_horizontal
    angle_y = norm_y * camera_fov_vertical

    # Camera depression: airframe pitch, mount offset, then the object's
    # own angle within the frame.
    pitch_deg = math.degrees(pitch)
    total_pitch = pitch_deg + camera_pitch_offset + angle_y

    total_pitch_rad = math.radians(total_pitch)
    roll_rad = roll

    # Split the slant range into its horizontal and vertical parts.
    horizontal_distance = distance_m * math.cos(total_pitch_rad)
    vertical_offset = distance_m * math.sin(total_pitch_rad)

    # Roll shortens the horizontal component.
    horizontal_distance_corrected = horizontal_distance * math.cos(roll_rad)

    # Bearing to the object: airframe heading plus its horizontal angle.
    heading_rad = math.radians(heading)
    angle_x_rad = math.radians(angle_x)
    object_bearing = heading_rad + angle_x_rad

    # Resolve the bearing into north/east ground offsets.
    delta_north = horizontal_distance_corrected * math.cos(object_bearing)
    delta_east = horizontal_distance_corrected * math.sin(object_bearing)

    # Metres to degrees. Longitude degrees shrink towards the poles, so
    # scale them by the cosine of the latitude.
    meters_per_degree_lat = 111111.0
    meters_per_degree_lon = 111111.0 * math.cos(math.radians(drone_lat))

    object_lat = drone_lat + delta_north / meters_per_degree_lat
    object_lon = drone_lon + delta_east / meters_per_degree_lon

    # Ground distance from the drone to the object.
    ground_distance = math.sqrt(delta_north ** 2 + delta_east ** 2)

    return object_lat, object_lon, ground_distance


def project_simple(drone_lat, drone_lon, heading_deg, distance_m):
    """
    Simple projection: object is straight ahead of drone at heading direction.

    Use this when drone attitude data is not available or for quick testing.
    Assumes level flight and camera pointing forward.
    """
    if distance_m is None or distance_m <= 0:
        return None, None, None

    heading_rad = math.radians(heading_deg)

    # Straight along the heading, level flight.
    delta_north = distance_m * math.cos(heading_rad)
    delta_east = distance_m * math.sin(heading_rad)

    meters_per_degree_lat = 111111.0
    meters_per_degree_lon = 111111.0 * math.cos(math.radians(drone_lat))

    object_lat = drone_lat + delta_north / meters_per_degree_lat
    object_lon = drone_lon + delta_east / meters_per_degree_lon

    return object_lat, object_lon, distance_m

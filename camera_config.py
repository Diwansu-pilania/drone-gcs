"""Camera configuration for detection projection

Adjust these parameters to match your camera specifications.
"""

# Camera field of view (degrees)
# Common values:
#   - GoPro Hero: 118° H, 69° V (wide mode)
#   - DJI Mavic: 84° H, 56° V
#   - Generic action cam: 90° H, 60° V
CAMERA_FOV_HORIZONTAL = 90.0
CAMERA_FOV_VERTICAL = 60.0

# Camera mount pitch offset (degrees)
# Positive = tilted down, Negative = tilted up
# Common values:
#   - Straight down (nadir): -90.0
#   - 45° downward: -45.0
#   - Forward-facing: 0.0
CAMERA_PITCH_OFFSET = -90.0

# Image resolution (pixels)
# Must match the resolution of images sent to detection server
IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080

# Detection projection settings
# Maximum time difference (seconds) for syncing detection with drone position
MAX_SYNC_TIME_DIFF = 3600  # Increased to 1 hour for testing

# Enable/disable projection (if False, uses drone position directly)
ENABLE_PROJECTION = True

# Debug mode: prints projection details to console
DEBUG_PROJECTION = False

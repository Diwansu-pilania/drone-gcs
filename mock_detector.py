"""Mock detector - sends fake object detections to test the dashboard.

Run this AFTER starting the GCS (python main.py). It simulates an object
detector POSTing detections to the dashboard's API using the NEW schema and
sends the image as a multipart file via httpx (same contract your real
detector should use).

Usage:
    python mock_detector.py
"""
import time
import random
import io
import json
from datetime import datetime

import httpx

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

API_URL = "http://127.0.0.1:5000/detection"

# Object classes to simulate
OBJECT_CLASSES = ["person", "car", "truck", "building", "boat", "animal"]

# Simulated flight area (around Delhi)
BASE_LAT = 28.6139
BASE_LON = 77.2090

_track_counter = 0


def make_test_image(label):
    """Generate a simple test image with the label drawn on it -> JPEG bytes."""
    if not HAS_PIL:
        return None

    img = Image.new("RGB", (320, 240), color=(random.randint(50, 200),
                                              random.randint(50, 200),
                                              random.randint(50, 200)))
    draw = ImageDraw.Draw(img)
    draw.rectangle([60, 50, 260, 190], outline=(255, 0, 0), width=3)
    draw.text((70, 60), f"{label}", fill=(255, 255, 255))
    draw.text((70, 200), datetime.now().strftime("%H:%M:%S"), fill=(255, 255, 0))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


def send_detection():
    """Send a single random detection to the API (new schema, via httpx)."""
    global _track_counter
    _track_counter += 1

    label = random.choice(OBJECT_CLASSES)
    now = datetime.now()
    epoch = int(now.timestamp())
    image_file = f"{label}_id{_track_counter}_{epoch}.jpg"

    # Roughly half the detections have a GPS fix, half don't (gps: null).
    if random.random() < 0.5:
        gps = {"lat": BASE_LAT + random.uniform(-0.005, 0.005),
               "lon": BASE_LON + random.uniform(-0.005, 0.005)}
        altitude = round(random.uniform(30, 100), 1)
    else:
        gps = None
        altitude = None

    payload = {
        "track_id": _track_counter,
        "class": label,
        "confidence": round(random.uniform(0.4, 0.99), 4),
        "bbox": {"x1": 506, "y1": 1541, "x2": 557, "y2": 1600},
        "timestamp": epoch,
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "image_file": image_file,
        "gps": gps,
        "altitude": altitude,
        "depth": {"status": "complete",
                  "distance_m": round(random.uniform(2, 40), 6)},
    }

    image_bytes = make_test_image(label)

    try:
        if image_bytes:
            # Multipart: full JSON in the "detection" field + image file part.
            response = httpx.post(
                API_URL,
                data={"detection": json.dumps(payload)},
                files={"image": (image_file, image_bytes, "image/jpeg")},
                timeout=5,
            )
        else:
            response = httpx.post(API_URL, json=payload, timeout=5)

        if response.status_code == 200:
            where = (f"{gps['lat']:.5f}, {gps['lon']:.5f}" if gps else "no GPS")
            print(f"✓ Sent {label} (#{_track_counter}) [{where}]")
        else:
            print(f"✗ Failed: {response.status_code} {response.text}")
    except httpx.ConnectError:
        print("✗ Cannot connect to GCS. Is main.py running?")
    except Exception as e:
        print(f"✗ Error: {e}")


def main():
    print("Mock detector started. Sending detections every 3-6 seconds.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            send_detection()
            time.sleep(random.uniform(3, 6))
    except KeyboardInterrupt:
        print("\nMock detector stopped.")


if __name__ == "__main__":
    main()

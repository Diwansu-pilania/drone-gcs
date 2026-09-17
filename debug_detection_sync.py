"""Debug script to check flight path and detection sync"""
import sys
from datetime import datetime

# Check if there's a running GCS instance we can inspect
print("=== Detection Sync Debugging ===\n")

# Parse the last detection
last_detection = {
    "timestamp": "2026-08-14T11:39:00",
    "class": "pedestrian",
    "has_gps": False
}

detection_time = datetime.fromisoformat(last_detection["timestamp"])
current_time = datetime.now()

print(f"Detection timestamp: {detection_time}")
print(f"Current time:        {current_time}")
print(f"Time difference:     {(current_time - detection_time).total_seconds():.1f} seconds\n")

print("PROBLEM DIAGNOSIS:")
print("─" * 60)

time_diff = (current_time - detection_time).total_seconds()

if abs(time_diff) > 30:
    print("❌ Detection timestamp is too old/future!")
    print(f"   The detection is {abs(time_diff):.0f} seconds away from now.")
    print(f"   Position sync only works within ±30 seconds.\n")

    print("SOLUTIONS:")
    print("1. Send a NEW detection with current timestamp:")
    print("   curl -X POST http://100.115.65.40:5000/detection \\")
    print("     -H 'Content-Type: application/json' \\")
    print(f"     -d '{{\"class\":\"test\",\"confidence\":0.9,\"timestamp\":{int(current_time.timestamp())},\"bbox\":{{\"x1\":100,\"y1\":100,\"x2\":200,\"y2\":200}}}}'")
    print()
    print("2. OR increase time window in main_window.py:")
    print("   Change max_time_diff_sec=30 to max_time_diff_sec=3600")
    print()
else:
    print("✅ Detection timestamp is recent enough")
    print("   The issue is likely that the drone's flight_path is empty.")
    print("   Make sure the drone is connected and sending GPS positions.\n")

print("\nTo check if drone is sending positions, look in the GCS UI:")
print("  - Left panel should show Latitude/Longitude values")
print("  - Map should show a blue drone marker")
print("  - Flight path (blue line) should be drawing")

# Drone GCS - Real-Time Dashboard

A PyQt6-based ground control station (GCS) for drones that displays live telemetry, flight path tracking, and object detection overlays in real-time.

![Dashboard Preview](docs/screenshot.png)

## Features

### Live Telemetry Display
- **Position**: GPS coordinates (lat/lon), altitude (MSL and relative)
- **Attitude**: Roll, pitch, yaw with visual compass showing heading
- **Speed**: Airspeed and groundspeed
- **Battery**: Voltage, current, remaining percentage with color-coded warnings
- **GPS Status**: Fix type (3D/2D/None) and satellite count
- **Flight Status**: Current mode (AUTO, LOITER, RTL, etc.) and armed state

### Interactive Map
- Real-time drone position with heading indicator
- Flight path trail (last 500 positions)
- Satellite/street map tiles (OpenStreetMap)
- Auto-centering when drone approaches viewport edge

### Object Detection Integration
- HTTP API endpoint for object detections from your detector
- Markers on map showing detected objects with:
  - Object class and confidence
  - Drone position/altitude/heading when detected
  - Timestamp
  - Detection image thumbnail
- Scrollable detection history panel

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Main Window                         │
│  ┌──────────────┬──────────────────┬─────────────────┐ │
│  │  Telemetry   │    Map Widget    │   Detections    │ │
│  │   Panel      │   (Leaflet)      │     Panel       │ │
│  │              │                  │                 │ │
│  │ • Position   │  • Drone marker  │ • Detection     │ │
│  │ • Compass    │  • Flight path   │   list          │ │
│  │ • Battery    │  • Object pins   │ • Thumbnails    │ │
│  │ • GPS        │  • Heading line  │ • Metadata      │ │
│  └──────────────┴──────────────────┴─────────────────┘ │
└─────────────────────────────────────────────────────────┘
              ▲                           ▲
              │ MAVLink Thread            │ Flask API
              │ (QThread)                 │ (background)
              │                           │
      ┌───────┴──────┐          ┌────────┴─────────┐
      │  Drone via   │          │ Object Detector  │
      │ Serial/Radio │          │  (Your Code)     │
      └──────────────┘          └──────────────────┘
```

## Installation

### Requirements
- Python 3.8+
- Windows/Linux/macOS
- Drone with MAVLink telemetry (serial/USB/radio) or SITL simulator

### Setup

1. **Clone or download this project**
```bash
cd drone-gcs
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Configure connection** (optional)
Edit `config.py`:
```python
MAVLINK_CONNECTION = "COM3"  # Change to your serial port
BAUD_RATE = 57600           # Or 115200 for some radios
```

## Usage

### 1. Start the Dashboard

```bash
python main.py
```

The detection API server starts automatically on `http://127.0.0.1:5000`

### 2. Connect to Your Drone

- Click **Connect** in the toolbar
- Select connection type:
  - **Serial**: USB or telemetry radio (select COM port)
  - **UDP**: Network connection (e.g., `127.0.0.1:14550` for SITL)
  - **TCP**: TCP connection
- Click **Connect**

### 3. Send Object Detections

Your object detector should POST to `http://127.0.0.1:5000/detection`

**JSON format:**
```json
{
  "object_class": "person",
  "confidence": 0.92,
  "latitude": 28.6139,
  "longitude": 77.2090,
  "altitude": 50.0,
  "heading": 135.0,
  "timestamp": "2026-08-05T12:34:56",
  "image_base64": "<base64 encoded JPEG>"
}
```

**Or multipart/form-data:**
```bash
curl -X POST http://127.0.0.1:5000/detection \
  -F "object_class=car" \
  -F "confidence=0.85" \
  -F "latitude=28.6140" \
  -F "longitude=77.2091" \
  -F "altitude=45.5" \
  -F "heading=270" \
  -F "image=@detection.jpg"
```

### 4. Test with Mock Detector

Run the mock detector to generate fake detections:
```bash
python mock_detector.py
```

This sends random detections every 3-6 seconds with generated test images.

## Configuration

Edit `config.py` to customize:

```python
# MAVLink connection
MAVLINK_CONNECTION = "COM3"  # or "udp:127.0.0.1:14550"
BAUD_RATE = 57600

# Detection API
DETECTION_API_HOST = "127.0.0.1"
DETECTION_API_PORT = 5000

# Map defaults
DEFAULT_MAP_CENTER = [28.6139, 77.2090]  # [lat, lon]
DEFAULT_MAP_ZOOM = 15

# Image storage
DETECTION_IMAGE_DIR = "detection_images"
```

## Connecting to Real Hardware

### USB/Serial Connection
1. Connect drone flight controller or telemetry radio via USB
2. Find COM port:
   - **Windows**: Device Manager → Ports → note COM number
   - **Linux**: `ls /dev/ttyUSB*` or `/dev/ttyACM*`
3. Set `MAVLINK_CONNECTION = "COM3"` (or your port) in `config.py`
4. Start dashboard and click Connect

### Telemetry Radio (Serial)
- Use a 433/915 MHz telemetry pair (e.g., SiK radio, 3DR Radio)
- Connect receiver radio to PC via USB
- Set appropriate baud rate (usually 57600)
- Configure as serial connection

### Network (UDP/TCP)
- For WiFi/ethernet telemetry modules
- Use `udp:IP:PORT` or `tcp:IP:PORT` format
- Example: `udp:192.168.1.100:14550`

## Integrating Your Object Detector

### Python Example

```python
import requests
import base64
from datetime import datetime

def send_detection(image_path, obj_class, confidence, lat, lon, alt, heading):
    with open(image_path, 'rb') as f:
        image_b64 = base64.b64encode(f.read()).decode('utf-8')
    
    payload = {
        "object_class": obj_class,
        "confidence": confidence,
        "latitude": lat,
        "longitude": lon,
        "altitude": alt,
        "heading": heading,
        "timestamp": datetime.now().isoformat(),
        "image_base64": image_b64
    }
    
    response = requests.post("http://127.0.0.1:5000/detection", json=payload)
    return response.json()

# Usage
send_detection(
    "detected_person.jpg",
    "person",
    0.92,
    28.6139,  # drone's current latitude
    77.2090,  # drone's current longitude
    50.0,     # drone's current altitude
    135.0     # drone's current heading
)
```

### Getting Drone State for Detections

Your detector needs the drone's current position/heading when an object is detected. Two approaches:

**Option 1: Query the vehicle state**
Add to `core/detection_server.py`:
```python
@app.route("/drone_state", methods=["GET"])
def get_drone_state():
    return jsonify({
        "latitude": self.vehicle_state.latitude,
        "longitude": self.vehicle_state.longitude,
        "altitude": self.vehicle_state.altitude,
        "heading": self.vehicle_state.heading
    })
```

**Option 2: Receive via MAVLink directly**
Your detector can also connect to MAVLink:
```python
from pymavlink import mavutil

master = mavutil.mavlink_connection('udp:127.0.0.1:14550')
master.wait_heartbeat()

# Get position
msg = master.recv_match(type='GLOBAL_POSITION_INT', blocking=True)
lat = msg.lat / 1e7
lon = msg.lon / 1e7
heading = msg.hdg / 100.0
```

## Troubleshooting

### Connection Issues

**"No ports found"**
- Ensure drone/radio is connected via USB
- Install driver (Silicon Labs CP210x or FTDI)
- Try refreshing ports in connection dialog

**"Connection lost"**
- Check baud rate matches telemetry settings
- Verify MAVLink is enabled on flight controller
- Check cable/radio connection

**"Waiting for heartbeat..."**
- Flight controller may be off or not sending MAVLink
- Wrong port or baud rate
- Try different connection string

### Map Not Loading

- Check internet connection (map tiles from OpenStreetMap)
- WebEngine issue: ensure PyQt6-WebEngine is installed
- Check browser console in developer tools

### Detections Not Appearing

- Verify detection API is running (check status bar)
- Test endpoint: `curl http://127.0.0.1:5000/health`
- Check detection format matches spec
- Look for errors in terminal

## Project Structure

```
drone-gcs/
├── main.py                  # Entry point
├── config.py                # Configuration
├── requirements.txt         # Dependencies
├── mock_detector.py         # Test detection generator
├── core/
│   ├── vehicle_state.py     # Vehicle state model with Qt signals
│   ├── mavlink_thread.py    # MAVLink message processing (QThread)
│   └── detection_server.py  # Flask API for detections
├── ui/
│   ├── main_window.py       # Main window and connection dialog
│   ├── map_widget.py        # Map widget (QWebEngineView + Leaflet)
│   ├── telemetry_panel.py   # Live telemetry display + compass
│   └── detection_panel.py   # Detection list with thumbnails
└── web/
    └── map.html             # Leaflet map HTML
```

## API Reference

### Detection API

**POST /detection**
Submit a detected object.

Request body (JSON):
```json
{
  "object_class": "string",      // Required
  "confidence": 0.0-1.0,         // Required
  "latitude": float,             // Required
  "longitude": float,            // Required
  "altitude": float,             // Required (meters)
  "heading": 0-360,              // Required (degrees, 0=North)
  "timestamp": "ISO 8601",       // Optional (auto-filled)
  "image_base64": "string"       // Optional (base64 JPEG)
}
```

Response:
```json
{
  "status": "ok",
  "id": 1
}
```

**GET /detections**
List all detections received this session.

**GET /health**
Check API status.

## MAVLink Messages Used

- `HEARTBEAT` - Connection status, armed state, flight mode
- `GLOBAL_POSITION_INT` - GPS position, heading, velocity
- `ATTITUDE` - Roll, pitch, yaw
- `GPS_RAW_INT` - GPS fix type, satellite count
- `SYS_STATUS` - Battery voltage/current/remaining
- `VFR_HUD` - Airspeed, groundspeed

## Performance

- Map updates: 5 Hz (configurable via `map_timer` interval)
- MAVLink processing: Continuous (blocking with 1s timeout)
- Detection API: Flask threaded mode
- Flight path: Last 500 points retained

## Known Limitations

- Map requires internet (OpenStreetMap tiles)
- No mission upload/download (view-only)
- No parameter editing
- Single vehicle only
- Detection images stored in memory (cleared on restart)

## Future Enhancements

- [ ] Offline map tiles
- [ ] Mission planning/upload
- [ ] Parameter editor
- [ ] Multi-vehicle support
- [ ] Replay mode from logs
- [ ] Custom map markers/overlays
- [ ] Geofence display
- [ ] Video stream integration

## License

MIT License - See LICENSE file

## Contributing

Pull requests welcome! Areas needing work:
- Additional MAVLink message support
- Better error handling
- Unit tests
- Documentation improvements
- Cross-platform testing

## Credits

Built with:
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) - GUI framework
- [pymavlink](https://github.com/ArduPilot/pymavlink) - MAVLink protocol
- [Leaflet](https://leafletjs.com/) - Interactive maps
- [Flask](https://flask.palletsprojects.com/) - Detection API
- [OpenStreetMap](https://www.openstreetmap.org/) - Map tiles

## Support

For issues or questions:
1. Check Troubleshooting section above
2. Review MAVLink connection in Mission Planner/QGC first
3. Open an issue with logs and steps to reproduce

---

**Happy flying! 🚁**
#   d r o n e - g c s  
 
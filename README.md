# 🚁 Drone GCS — Real-Time Dashboard

A **PyQt6-based Ground Control Station (GCS)** for drones that provides real-time telemetry, flight-path tracking, interactive mapping, and object-detection visualization.

![Dashboard Preview](docs/screenshot.png)

---

## ✨ Features

### 📡 Live Telemetry

* **Position:** GPS latitude/longitude, MSL altitude, and relative altitude
* **Attitude:** Roll, pitch, and yaw
* **Heading:** Visual compass
* **Speed:** Airspeed and groundspeed
* **Battery:** Voltage, current, and remaining percentage with color-coded warnings
* **GPS Status:** Fix type and satellite count
* **Flight Status:** Current flight mode (`AUTO`, `LOITER`, `RTL`, etc.) and armed state

### 🗺️ Interactive Map

* Real-time drone position
* Heading indicator
* Flight-path trail with the last 500 positions
* Satellite/street map tiles using OpenStreetMap
* Automatic map centering when the drone approaches the viewport edge

### 🎯 Object Detection Integration

* HTTP API endpoint for object detections
* Detection markers displayed on the map
* Detection information includes:

  * Object class
  * Confidence score
  * Drone position
  * Altitude
  * Heading
  * Timestamp
  * Detection image thumbnail
* Scrollable detection history panel

---

## 🏗️ Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     Main Window                         │
│                                                         │
│  ┌──────────────┬──────────────────┬─────────────────┐ │
│  │  Telemetry   │    Map Widget    │   Detections    │ │
│  │    Panel     │    (Leaflet)     │      Panel      │ │
│  │              │                  │                 │ │
│  │ • Position   │ • Drone marker  │ • Detection     │ │
│  │ • Compass    │ • Flight path   │   list          │ │
│  │ • Battery    │ • Object pins   │ • Thumbnails    │ │
│  │ • GPS        │ • Heading line  │ • Metadata      │ │
│  └──────────────┴──────────────────┴─────────────────┘ │
└─────────────────────────────────────────────────────────┘
             ▲                           ▲
             │ MAVLink Thread            │ Flask API
             │ (QThread)                 │ (Background)
             │                           │
     ┌───────┴──────┐           ┌────────┴─────────┐
     │  Drone via   │           │ Object Detector  │
     │ Serial/Radio │           │    (Your Code)   │
     └──────────────┘           └──────────────────┘
```

---

## 🛠️ Installation

### Requirements

* Python 3.8+
* Windows / Linux / macOS
* Drone with MAVLink telemetry
* Serial, USB, radio, UDP, or TCP connection
* Alternatively, a SITL simulator

### Setup

#### 1. Clone or Download the Project

```bash
git clone <your-repository-url>
cd drone-gcs
```

#### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

#### 3. Configure the MAVLink Connection

Edit `config.py`:

```python
MAVLINK_CONNECTION = "COM3"
BAUD_RATE = 57600
```

Change `COM3` to the appropriate serial port for your system.

---

## 🚀 Usage

### 1. Start the Dashboard

```bash
python main.py
```

The detection API server starts automatically at:

```text
http://127.0.0.1:5000
```

### 2. Connect to Your Drone

Click **Connect** in the toolbar and select the appropriate connection type.

#### Serial

USB or telemetry radio:

```text
COM3
```

#### UDP

For SITL or network-based telemetry:

```text
udp:127.0.0.1:14550
```

#### TCP

Use the appropriate TCP connection string.

---

## 🎯 Sending Object Detections

Your object detector should send detections to:

```text
POST http://127.0.0.1:5000/detection
```

### JSON Request

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

### Multipart Form Data

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

---

## 🧪 Mock Detector

You can test the dashboard without a real object detector:

```bash
python mock_detector.py
```

The mock detector generates random detections every **3–6 seconds** with generated test images.

---

## ⚙️ Configuration

Edit `config.py` to customize the dashboard.

```python
# MAVLink connection
MAVLINK_CONNECTION = "COM3"
BAUD_RATE = 57600

# Detection API
DETECTION_API_HOST = "127.0.0.1"
DETECTION_API_PORT = 5000

# Map defaults
DEFAULT_MAP_CENTER = [28.6139, 77.2090]  # [latitude, longitude]
DEFAULT_MAP_ZOOM = 15

# Image storage
DETECTION_IMAGE_DIR = "detection_images"
```

---

## 🔌 Connecting to Real Hardware

### USB / Serial Connection

1. Connect the drone flight controller or telemetry radio via USB.
2. Find the assigned COM port.

**Windows:**

```text
Device Manager → Ports → COM Port
```

**Linux:**

```bash
ls /dev/ttyUSB*
ls /dev/ttyACM*
```

3. Configure the port in `config.py`:

```python
MAVLINK_CONNECTION = "COM3"
```

4. Start the dashboard.
5. Click **Connect**.

### 📻 Telemetry Radio

Compatible telemetry radios include:

* SiK radio
* 3DR Radio
* 433 MHz / 915 MHz telemetry systems

Typical configuration:

```text
Baud Rate: 57600
Connection: Serial
```

### 🌐 UDP / TCP

For Wi-Fi or Ethernet telemetry:

```text
udp:192.168.1.100:14550
```

or

```text
tcp:192.168.1.100:14550
```

---

## 🤖 Integrating Your Object Detector

### Python Example

```python
import base64
import requests

from datetime import datetime


def send_detection(
    image_path,
    obj_class,
    confidence,
    lat,
    lon,
    alt,
    heading
):
    with open(image_path, "rb") as file:
        image_b64 = base64.b64encode(file.read()).decode("utf-8")

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

    response = requests.post(
        "http://127.0.0.1:5000/detection",
        json=payload
    )

    return response.json()


send_detection(
    "detected_person.jpg",
    "person",
    0.92,
    28.6139,
    77.2090,
    50.0,
    135.0
)
```

---

## 📍 Getting Drone State for Detections

The detector needs the drone's current position and heading when an object is detected.

There are two approaches.

### Option 1: Query Vehicle State

Add the following endpoint to `core/detection_server.py`:

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

### Option 2: Receive MAVLink Directly

```python
from pymavlink import mavutil


master = mavutil.mavlink_connection(
    "udp:127.0.0.1:14550"
)

master.wait_heartbeat()

msg = master.recv_match(
    type="GLOBAL_POSITION_INT",
    blocking=True
)

lat = msg.lat / 1e7
lon = msg.lon / 1e7
heading = msg.hdg / 100.0
```

---

## 🔗 API Reference

### `POST /detection`

Submit a detected object.

#### Request

```json
{
  "object_class": "string",
  "confidence": 0.0,
  "latitude": 28.6139,
  "longitude": 77.2090,
  "altitude": 50.0,
  "heading": 135.0,
  "timestamp": "ISO 8601",
  "image_base64": "string"
}
```

#### Required Fields

| Field          | Type   | Description                    |
| -------------- | ------ | ------------------------------ |
| `object_class` | string | Detected object class          |
| `confidence`   | float  | Detection confidence, 0–1      |
| `latitude`     | float  | Drone latitude                 |
| `longitude`    | float  | Drone longitude                |
| `altitude`     | float  | Altitude in meters             |
| `heading`      | float  | Heading in degrees, 0° = North |

#### Optional Fields

| Field          | Type   | Description               |
| -------------- | ------ | ------------------------- |
| `timestamp`    | string | ISO 8601 timestamp        |
| `image_base64` | string | Base64-encoded JPEG image |

#### Response

```json
{
  "status": "ok",
  "id": 1
}
```

### `GET /detections`

Returns all detections received during the current session.

### `GET /health`

Checks whether the detection API is running.

---

## 📡 MAVLink Messages Used

| MAVLink Message       | Purpose                                            |
| --------------------- | -------------------------------------------------- |
| `HEARTBEAT`           | Connection status, armed state, flight mode        |
| `GLOBAL_POSITION_INT` | GPS position, heading, and velocity                |
| `ATTITUDE`            | Roll, pitch, and yaw                               |
| `GPS_RAW_INT`         | GPS fix type and satellite count                   |
| `SYS_STATUS`          | Battery voltage, current, and remaining percentage |
| `VFR_HUD`             | Airspeed and groundspeed                           |

---

## 📁 Project Structure

```text
drone-gcs/
│
├── main.py                    # Application entry point
├── config.py                  # Configuration
├── requirements.txt           # Dependencies
├── mock_detector.py           # Test detection generator
│
├── core/
│   ├── vehicle_state.py       # Vehicle state model
│   ├── mavlink_thread.py      # MAVLink processing
│   └── detection_server.py    # Flask detection API
│
├── ui/
│   ├── main_window.py         # Main window and connection dialog
│   ├── map_widget.py          # Map widget
│   ├── telemetry_panel.py     # Telemetry and compass
│   └── detection_panel.py     # Detection list and thumbnails
│
└── web/
    └── map.html               # Leaflet map
```

---

## ⚡ Performance

* **Map updates:** 5 Hz
* **MAVLink processing:** Continuous
* **Detection API:** Flask threaded mode
* **Flight path:** Last 500 points retained

---

## ⚠️ Known Limitations

* Map requires an internet connection for OpenStreetMap tiles
* No mission upload/download
* No parameter editing
* Single-vehicle support only
* Detection images are stored in memory and cleared after restart

---

## 🔮 Future Enhancements

* [ ] Offline map tiles
* [ ] Mission planning and upload
* [ ] Parameter editor
* [ ] Multi-vehicle support
* [ ] Replay mode from flight logs
* [ ] Custom map markers and overlays
* [ ] Geofence visualization
* [ ] Video-stream integration

---

## 📜 License

This project is licensed under the **MIT License**.

See the `LICENSE` file for details.

---

## 🤝 Contributing

Pull requests are welcome!

Areas where contributions are especially useful:

* Additional MAVLink message support
* Improved error handling
* Unit tests
* Documentation improvements
* Cross-platform testing

---

## 🧰 Built With

* [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — GUI framework
* [pymavlink](https://github.com/ArduPilot/pymavlink) — MAVLink protocol
* [Leaflet](https://leafletjs.com/) — Interactive maps
* [Flask](https://flask.palletsprojects.com/) — Detection API
* [OpenStreetMap](https://www.openstreetmap.org/) — Map tiles

---

## 🆘 Support

For issues or questions:

1. Check the **Troubleshooting** section.
2. Verify the MAVLink connection using Mission Planner or QGroundControl.
3. Open an issue with logs and clear reproduction steps.

---

<div align="center">

### Happy Flying! 🚁

</div>

# 🚁 Drone GCS — Real-Time Dashboard

A **PyQt6-based Ground Control Station (GCS)** for drones that provides real-time telemetry, flight-path tracking, interactive mapping, and object-detection visualization.

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


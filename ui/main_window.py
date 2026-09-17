"""Main window - assembles all panels and manages connection"""
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QSplitter, QDialog, QLabel, QComboBox, QLineEdit,
                              QPushButton, QFormLayout, QMessageBox, QToolBar)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtGui import QAction
import serial.tools.list_ports
import threading

from core.vehicle_state import VehicleState
from core.mavlink_thread import MAVLinkThread
from core.detection_server import DetectionServer
from core.detection_flow import prepare_detection_for_map
from core.checkpoint_client import (checkpoints_from_response,
                                    equipment_max_range_km,
                                    fetch_nearby_checkpoints,
                                    radius_from_equipment)
from core.tile_server import TileCacheGroup
from ui.map_widget import MapWidget
from ui.telemetry_panel import TelemetryPanel
from ui.detection_panel import DetectionPanel
import config


class ConnectionDialog(QDialog):
    """Dialog for selecting connection type and port"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Vehicle")
        self.setMinimumWidth(400)
        self.connection_string = None
        self.baud_rate = config.BAUD_RATE
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Connection type
        self.conn_type = QComboBox()
        self.conn_type.addItems(["Serial (USB/Radio)", "UDP", "TCP"])
        self.conn_type.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Connection Type:", self.conn_type)

        # Serial port selector
        self.port_combo = QComboBox()
        self._refresh_ports()
        form.addRow("Serial Port:", self.port_combo)

        # Refresh ports button
        refresh_btn = QPushButton("Refresh Ports")
        refresh_btn.clicked.connect(self._refresh_ports)
        form.addRow("", refresh_btn)

        # Baud rate
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["57600", "115200", "9600", "38400", "921600"])
        form.addRow("Baud Rate:", self.baud_combo)

        # Network address (for UDP/TCP)
        self.network_addr = QLineEdit("127.0.0.1:14550")
        self.network_addr.setEnabled(False)
        form.addRow("Network Address:", self.network_addr)

        layout.addLayout(form)

        # Buttons
        btn_layout = QHBoxLayout()
        connect_btn = QPushButton("Connect")
        connect_btn.clicked.connect(self._on_connect)
        connect_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(connect_btn)
        layout.addLayout(btn_layout)

    def _refresh_ports(self):
        """Refresh available serial ports"""
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(f"{port.device} - {port.description}", port.device)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("No ports found", None)

    def _on_type_changed(self, index):
        """Enable/disable fields based on connection type"""
        is_serial = (index == 0)
        self.port_combo.setEnabled(is_serial)
        self.baud_combo.setEnabled(is_serial)
        self.network_addr.setEnabled(not is_serial)

    def _on_connect(self):
        """Build connection string and accept dialog"""
        conn_type = self.conn_type.currentIndex()

        if conn_type == 0:  # Serial
            port = self.port_combo.currentData()
            if port is None:
                QMessageBox.warning(self, "Error", "No serial port selected")
                return
            self.connection_string = port
            self.baud_rate = int(self.baud_combo.currentText())
        elif conn_type == 1:  # UDP
            self.connection_string = f"udp:{self.network_addr.text()}"
        else:  # TCP
            self.connection_string = f"tcp:{self.network_addr.text()}"

        self.accept()


class MainWindow(QMainWindow):
    """Main application window"""

    # Emitted from the checkpoint worker thread so the markers are drawn on the
    # Qt thread. Touching widgets straight from the worker would be unsafe.
    checkpoints_ready = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Drone GCS - Real-Time Dashboard")
        self.setGeometry(100, 100, 1400, 800)

        # Core components
        self.vehicle_state = VehicleState()
        self.mavlink_thread = None
        self.detection_server = None

        # Only say "checkpoint lookup is off" once, not on every detection.
        self._checkpoint_hint_shown = False

        # Offline map tile cache (fetches when online, serves from disk when
        # not). Multi-layer: satellite imagery + label overlays + street map.
        default_layers = {
            "street": {
                "url": getattr(config, "MAP_TILE_URL",
                               "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
                "attribution": "© OpenStreetMap contributors",
                "max_zoom": 19, "base": True, "default": True,
            },
        }
        self.tile_cache = TileCacheGroup(
            config.TILE_CACHE_DIR,
            getattr(config, "MAP_TILE_LAYERS", default_layers),
            user_agent=getattr(config, "MAP_TILE_USER_AGENT", None),
        )

        # Pre-cache the offline home area (AIT campus) so the map works with no
        # internet. Runs in a daemon thread; skips tiles already on disk.
        if getattr(config, "PREFETCH_OFFLINE_TILES", False):
            self._start_offline_prefetch()

        # Base URL the map uses to reach the tile-cache + image endpoints
        self.api_base = f"http://{config.DETECTION_API_HOST}:{config.DETECTION_API_PORT}"

        self._init_ui()
        self._init_detection_server()
        self._connect_signals()

        # Timer to periodically update map with drone position
        self._showing_offline = None  # None = view not set yet; True/False track state
        self.map_timer = QTimer()
        self.map_timer.timeout.connect(self._update_map)
        self.map_timer.start(200)  # Update map 5 times per second

        # Auto-connect on startup if configured
        if getattr(config, "AUTO_CONNECT", False):
            QTimer.singleShot(500, lambda: self._connect(
                config.MAVLINK_CONNECTION, config.BAUD_RATE))

    def _init_ui(self):
        # Toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        connect_action = QAction("Connect", self)
        connect_action.triggered.connect(self._show_connection_dialog)
        toolbar.addAction(connect_action)

        disconnect_action = QAction("Disconnect", self)
        disconnect_action.triggered.connect(self._disconnect)
        toolbar.addAction(disconnect_action)

        toolbar.addSeparator()

        clear_path_action = QAction("Clear Path", self)
        clear_path_action.triggered.connect(self._clear_path)
        toolbar.addAction(clear_path_action)

        # Central widget with splitter layout
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Main splitter: [telemetry | map | detections]
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: telemetry panel
        self.telemetry_panel = TelemetryPanel(self.vehicle_state)
        self.telemetry_panel.setMinimumWidth(280)
        self.telemetry_panel.setMaximumWidth(350)
        splitter.addWidget(self.telemetry_panel)

        # Center: map (opens on the AIT home view)
        self.map_widget = MapWidget(
            api_base=self.api_base,
            center=getattr(config, "DEFAULT_MAP_CENTER", None),
            zoom=getattr(config, "DEFAULT_MAP_ZOOM", None),
            layers=getattr(config, "MAP_TILE_LAYERS", None),
        )
        splitter.addWidget(self.map_widget)

        # Right: detection panel
        self.detection_panel = DetectionPanel()
        self.detection_panel.setMinimumWidth(280)
        self.detection_panel.setMaximumWidth(350)
        splitter.addWidget(self.detection_panel)

        # Set splitter proportions
        splitter.setSizes([300, 800, 300])

        main_layout.addWidget(splitter)

        # Status bar
        self.statusBar().showMessage("Ready - Click Connect to start")

    def _init_detection_server(self):
        """Start the detection API server"""
        self.detection_server = DetectionServer(
            config.DETECTION_API_HOST,
            config.DETECTION_API_PORT,
            config.DETECTION_IMAGE_DIR,
            tile_cache=self.tile_cache,
            bind_host=getattr(config, "DETECTION_API_BIND_HOST", "0.0.0.0"),
        )
        self.detection_server.detection_received.connect(self._on_detection_received)
        self.detection_server.start()
        self.statusBar().showMessage(
            f"Detection API listening on port {config.DETECTION_API_PORT} "
            f"(all interfaces) — reachable via Tailscale"
        )

    def _connect_signals(self):
        """Connect vehicle state signals for map updates"""
        # Map updates from telemetry are handled by the timer; the checkpoint
        # results arrive from a worker thread and are drawn here.
        self.checkpoints_ready.connect(self._on_checkpoints_ready)

    def _show_connection_dialog(self):
        """Show connection dialog and connect"""
        dialog = ConnectionDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._connect(dialog.connection_string, dialog.baud_rate)

    def _connect(self, connection_string, baud_rate):
        """Connect to vehicle"""
        if self.mavlink_thread and self.mavlink_thread.isRunning():
            self._disconnect()

        self.mavlink_thread = MAVLinkThread(
            self.vehicle_state, connection_string, baud_rate
        )
        self.mavlink_thread.connection_established.connect(self._on_connected)
        self.mavlink_thread.connection_lost.connect(self._on_connection_lost)
        self.mavlink_thread.error_occurred.connect(self._on_error)
        self.mavlink_thread.start()

        self.statusBar().showMessage(f"Connecting to {connection_string}...")

    def _disconnect(self):
        """Disconnect from vehicle"""
        if self.mavlink_thread:
            self.mavlink_thread.stop()
            self.mavlink_thread = None
        self.statusBar().showMessage("Disconnected")

    @pyqtSlot()
    def _on_connected(self):
        """Handle successful connection"""
        self.statusBar().showMessage("Connected to vehicle")

    @pyqtSlot()
    def _on_connection_lost(self):
        """Handle connection loss"""
        self.statusBar().showMessage("Connection lost - waiting for heartbeat...")

    @pyqtSlot(str)
    def _on_error(self, error_msg):
        """Handle connection error"""
        self.statusBar().showMessage(f"Error: {error_msg}")
        QMessageBox.critical(self, "Connection Error", error_msg)

    @pyqtSlot(dict)
    def _on_detection_received(self, detection):
        """Handle one state update for a detection.

        The detector sends the image/metadata first and the depth result later.
        A target without depth must stay pending: placing it at the drone's GPS
        position makes the map show a false zero-distance marker and the later
        depth update creates a second point.
        """
        import camera_config

        result = prepare_detection_for_map(
            detection, self.vehicle_state, camera_config)

        # With projection enabled, a GPS-less detection is intentionally not
        # mapped until the second POST supplies a positive depth distance.
        if result["should_map"]:
            self.map_widget.add_detection(detection)
            # The object now has a position, so ask which checkpoints are
            # within reach of it.
            self._request_checkpoints(detection)
        # Add to detection panel (always)
        self.detection_panel.add_detection(detection)

        obj_class = detection.get("object_class") or detection.get("class") or "object"
        confidence = detection.get("confidence", 0.0)
        suffix = ""
        if result["depth_error"]:
            suffix = " - depth error"
        elif result["waiting_for_depth"]:
            suffix = " - waiting for depth"
        elif result.get("position_source") == "default_center":
            # Be explicit that this one is anchored to the default centre
            # rather than a real GPS fix.
            suffix = " - no MAVLink fix, using default position"
        self.statusBar().showMessage(
            f"Detection: {obj_class} ({confidence*100:.0f}%){suffix}"
        )

    @staticmethod
    def _detection_key(detection):
        """Identity used to tie a checkpoint set to its detection.

        Matches the key the map widget uses for detection markers, so the two
        stay in step when a detection is updated and re-queried.
        """
        return (detection.get("image_file") or detection.get("id")
                or detection.get("timestamp"))

    def _request_checkpoints(self, detection):
        """Ask the external service which checkpoints are near this object.

        The search radius is the detected equipment's maximum range converted
        from km to m, so the circle covers how far that equipment can reach.
        The request runs on a worker thread: the peer is reachable over
        Tailscale/LAN and a slow or absent one must not stall the UI.
        """
        api_base = getattr(config, "CHECKPOINT_API_BASE", "")
        if not api_base:
            if not self._checkpoint_hint_shown:
                self._checkpoint_hint_shown = True
                print("[checkpoints] Lookup is off - set CHECKPOINT_API_BASE "
                      "in config.py to your Tailscale/LAN peer to enable it.")
            return

        latitude = detection.get("latitude")
        longitude = detection.get("longitude")
        if latitude is None or longitude is None:
            return

        default_radius = getattr(config, "CHECKPOINT_DEFAULT_RADIUS_M", 5000.0)
        radius_m = radius_from_equipment(detection, default_radius)
        used_equipment_range = equipment_max_range_km(detection) is not None
        timeout = getattr(config, "CHECKPOINT_API_TIMEOUT", 5.0)

        equipment_info = detection.get("equipment_info")
        equipment_name = (equipment_info or {}).get("name") \
            if isinstance(equipment_info, dict) else None
        object_class = (detection.get("object_class")
                        or detection.get("class") or "object")

        request = {
            "key": self._detection_key(detection),
            "center": {"latitude": latitude, "longitude": longitude},
            "radius_m": radius_m,
            "object_class": object_class,
            "equipment_name": equipment_name,
            "used_equipment_range": used_equipment_range,
        }

        if not used_equipment_range:
            print(f"[checkpoints] {object_class}: no equipment_info "
                  f"max_range_km, falling back to {radius_m:.0f} m")

        def run():
            payload = fetch_nearby_checkpoints(
                api_base, latitude, longitude, radius_m, timeout=timeout)
            if payload is None:
                return
            result = dict(request)
            result["checkpoints"] = checkpoints_from_response(payload)
            result["checkpoint_count"] = payload.get(
                "checkpoint_count", len(result["checkpoints"]))
            self.checkpoints_ready.emit(result)

        threading.Thread(target=run, daemon=True).start()

    @pyqtSlot(dict)
    def _on_checkpoints_ready(self, data):
        """Draw and log the checkpoints found near a detected object."""
        self.map_widget.add_checkpoints(data)

        checkpoints = data.get("checkpoints") or []
        count = data.get("checkpoint_count", len(checkpoints))
        radius_km = data.get("radius_m", 0.0) / 1000.0
        label = data.get("equipment_name") or data.get("object_class")
        origin = ("equipment max range" if data.get("used_equipment_range")
                  else "default radius")
        center = data.get("center") or {}

        print(f"[checkpoints] {label}: {count} within {radius_km:.2f} km "
              f"({origin}) of "
              f"{center.get('latitude')}, {center.get('longitude')}")
        for checkpoint in checkpoints:
            if isinstance(checkpoint, dict):
                print(f"[checkpoints]   - "
                      f"{checkpoint.get('name') or checkpoint.get('id')}")

        self.statusBar().showMessage(
            f"{label}: {count} checkpoint(s) within {radius_km:.2f} km"
        )

    def _update_map(self):
        """Update the map.

        When the drone has a live GPS fix, follow it. Otherwise (drone
        disconnected / no fix, or the system is offline) fall back to the
        Army Institute of Technology home view, zoomed in.
        """
        connected = self.vehicle_state.get_connection_status()
        lat = self.vehicle_state.latitude
        lon = self.vehicle_state.longitude
        has_fix = connected and (lat or lon)  # treat (0, 0) as "no fix"

        if has_fix:
            if self._showing_offline is not False:
                self._showing_offline = False
            self.map_widget.update_drone_position(
                lat, lon,
                self.vehicle_state.altitude,
                self.vehicle_state.heading,
            )
        else:
            # Show the AIT home view once, so the user can still pan/zoom freely.
            if self._showing_offline is not True:
                self._showing_offline = True
                center = getattr(config, "OFFLINE_MAP_CENTER", [18.6182667, 73.8767611])
                zoom = getattr(config, "OFFLINE_MAP_ZOOM", 17)
                self.map_widget.show_offline_view(center[0], center[1], zoom)

    def _start_offline_prefetch(self):
        """Pre-cache tiles around the AIT offline center in a background thread."""
        center = getattr(config, "OFFLINE_MAP_CENTER", [18.607139911174386, 73.87508649590356])
        zooms = getattr(config, "OFFLINE_PREFETCH_ZOOMS", [14, 15, 16, 17, 18, 19])
        radius = getattr(config, "OFFLINE_PREFETCH_RADIUS", 2)

        # Fetch the levels the user hits first — the default view and zooming in
        # — before backfilling the wider-area low zooms, so an immediate zoom-in
        # after startup finds tiles already cached instead of "loading".
        default_zoom = getattr(config, "DEFAULT_MAP_ZOOM", 17)
        zooms = sorted(zooms, key=lambda z: (z < default_zoom, abs(z - default_zoom)))

        def run():
            try:
                n = self.tile_cache.prefetch_area(center[0], center[1], zooms, radius)
                print(f"[tiles] Offline prefetch done: {n} new AIT tiles cached "
                      f"({self.tile_cache.cached_tile_count()} total).")
            except Exception as e:  # never let prefetch crash startup
                print(f"[tiles] Offline prefetch failed: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _clear_path(self):
        """Clear flight path"""
        self.map_widget.clear_flight_path()
        self.vehicle_state.flight_path = []

    def closeEvent(self, event):
        """Clean up on close"""
        if self.mavlink_thread:
            self.mavlink_thread.stop()
        event.accept()

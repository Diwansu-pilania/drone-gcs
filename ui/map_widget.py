"""Map widget - embeds Leaflet map via QWebEngineView with Qt↔JavaScript bridge"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, QUrlQuery, QObject, pyqtSlot
import os
import json


class MapWidget(QWidget):
    """Interactive map showing drone position, flight path, and detection markers"""

    def __init__(self, api_base=None, center=None, zoom=None, layers=None):
        super().__init__()
        self.detections = []
        self.api_base = api_base  # e.g. "http://127.0.0.1:5000" for offline tiles
        self.center = center      # [lat, lon] initial view; None -> map.html default
        self.zoom = zoom          # initial zoom level
        self.layers = layers      # dict of tile-layer specs (see config.MAP_TILE_LAYERS)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create web view
        self.web_view = QWebEngineView()

        # map.html is loaded as a file:// page but pulls tiles from the
        # http://127.0.0.1:5000/tiles endpoint. By default QtWebEngine blocks
        # local (file://) pages from loading remote URLs, which leaves the map
        # background blank while the markers (pure HTML divIcons) still render.
        # Allow the local page to reach the tile/image endpoints.
        settings = self.web_view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        # Load the Leaflet map HTML, passing the API base so the map can pull
        # tiles from the offline cache endpoint.
        map_path = os.path.join(os.path.dirname(__file__), "..", "web", "map.html")
        map_path = os.path.abspath(map_path)

        # Pass the tile-cache endpoint AND the initial view center/zoom so the
        # page doesn't hard-code them.
        query = QUrlQuery()
        if self.api_base:
            query.addQueryItem("api", self.api_base)
        if self.center:
            query.addQueryItem("center", f"{self.center[0]},{self.center[1]}")
        if self.zoom:
            query.addQueryItem("zoom", str(self.zoom))
        if self.layers:
            # JSON-encode the layer specs; QUrlQuery percent-encodes as needed.
            query.addQueryItem("layers", json.dumps(self.layers))

        url = QUrl.fromLocalFile(map_path)
        url.setQuery(query)
        self.web_view.setUrl(url)

        layout.addWidget(self.web_view)

    def update_drone_position(self, lat, lon, alt, heading):
        """Update drone marker position and heading on map"""
        js_code = f"window.updateDronePosition({lat}, {lon}, {alt}, {heading});"
        self.web_view.page().runJavaScript(js_code)

    def add_detection(self, detection_data):
        """Add a detection marker to the map

        Args:
            detection_data: dict with keys: latitude, longitude, object_class,
                          confidence, timestamp, image_url, heading, altitude
        """
        key = (detection_data.get("image_file") or detection_data.get("id")
               or detection_data.get("timestamp"))
        for index, existing in enumerate(self.detections):
            existing_key = (existing.get("image_file") or existing.get("id")
                            or existing.get("timestamp"))
            if key is not None and existing_key == key:
                self.detections[index] = detection_data
                break
        else:
            self.detections.append(detection_data)

        # Escape strings for JavaScript
        json_str = json.dumps(detection_data)
        js_code = f"window.addDetection({json_str});"
        self.web_view.page().runJavaScript(js_code)

    def clear_detections(self):
        """Remove all detection markers from map"""
        self.detections = []
        js_code = "window.clearDetections();"
        self.web_view.page().runJavaScript(js_code)

    def center_on_drone(self, lat, lon, zoom=15):
        """Center map on given coordinates"""
        js_code = f"window.centerMap({lat}, {lon}, {zoom});"
        self.web_view.page().runJavaScript(js_code)

    def show_offline_view(self, lat, lon, zoom):
        """Snap the map to the offline home view (AIT campus)"""
        js_code = f"window.showOfflineView({lat}, {lon}, {zoom});"
        self.web_view.page().runJavaScript(js_code)

    def clear_flight_path(self):
        """Clear the flight path trail"""
        js_code = "window.clearFlightPath();"
        self.web_view.page().runJavaScript(js_code)

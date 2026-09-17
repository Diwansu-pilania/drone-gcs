"""Detection panel - scrollable list of detected objects with thumbnails"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QFrame, QPushButton, QGroupBox)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPixmap, QImage
import requests
from datetime import datetime


def detection_key(d):
    """Stable identity shared by a detection's initial + depth POSTs."""
    return d.get("image_file") or d.get("timestamp") or d.get("id")


class DetectionItemWidget(QWidget):
    """Single detection item display; rebuilt in place when depth arrives."""

    def __init__(self, detection):
        super().__init__()
        self._thumb = None
        self._loaded_image_url = None
        self._init_ui()
        self.set_data(detection)
        self.setStyleSheet("""
            DetectionItemWidget {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
            }
            DetectionItemWidget:hover {
                background: #f5f5f5;
                border: 1px solid #2196F3;
            }
        """)

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self._thumb = QLabel()
        self._thumb.setFixedSize(80, 60)
        self._thumb.setStyleSheet("border: 1px solid #ccc; background: #f5f5f5;")
        self._thumb.setScaledContents(True)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._thumb)

        info = QVBoxLayout()
        self._title = QLabel()
        self._dist = QLabel();  self._dist.setStyleSheet("font-size: 11px; color: #666;")
        self._pos = QLabel();   self._pos.setStyleSheet("font-size: 11px; color: #666;")
        self._alt = QLabel();   self._alt.setStyleSheet("font-size: 11px; color: #666;")
        self._time = QLabel();  self._time.setStyleSheet("font-size: 11px; color: #999;")
        for w in (self._title, self._dist, self._pos, self._alt, self._time):
            info.addWidget(w)
        layout.addLayout(info, 1)

    def set_data(self, det):
        """Populate/refresh all fields from a (possibly merged) detection dict."""
        self.detection = det

        obj_class = det.get("object_class") or det.get("class") or "unknown"
        title = f"<b>{obj_class}</b> ({det.get('confidence', 0.0) * 100:.1f}%)"
        if det.get("track_id") is not None:
            title += f"  <span style='color:#888;'>#{det['track_id']}</span>"
        self._title.setText(title)

        # Depth / distance to target
        distance = det.get("distance_m")
        if distance is None and isinstance(det.get("depth"), dict):
            distance = det["depth"].get("distance_m")
        depth_status = (det.get("depth") or {}).get("status")
        try:
            positive_distance = distance is not None and float(distance) > 0
        except (TypeError, ValueError):
            positive_distance = False

        if positive_distance:
            self._dist.setText(f"📏 {float(distance):.2f} m")
        elif depth_status == "processing":
            self._dist.setText("📏 depth processing…")
        elif depth_status == "error":
            self._dist.setText("📏 depth error")
        else:
            self._dist.setText("")

        # Position (only if the detection carried GPS)
        if det.get("has_gps"):
            self._pos.setText(f"📍 {det['latitude']:.6f}, {det['longitude']:.6f}")
        else:
            self._pos.setText("📍 No GPS")

        if det.get("altitude"):
            self._alt.setText(f"↕️ {det['altitude']:.1f} m")
        else:
            self._alt.setText("")

        self._time.setText(f"🕐 {self._format_time(det.get('timestamp'))}")

        # Load thumbnail once, when an image_url first appears.
        image_url = det.get("image_url")
        if image_url and image_url != self._loaded_image_url:
            self._load_thumbnail(image_url)

    def _load_thumbnail(self, image_url):
        try:
            resp = requests.get(image_url, timeout=2)
            if resp.status_code == 200:
                image = QImage()
                image.loadFromData(resp.content)
                self._thumb.setPixmap(QPixmap.fromImage(image))
                self._loaded_image_url = image_url
            else:
                self._thumb.setText("IMG")
        except Exception:
            self._thumb.setText("IMG")

    @staticmethod
    def _format_time(ts):
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts)
            else:
                dt = datetime.fromisoformat(ts)
            return dt.strftime("%H:%M:%S")
        except Exception:
            return "Unknown time"


class DetectionPanel(QWidget):
    """Panel displaying list of all detected objects"""

    def __init__(self):
        super().__init__()
        self.detections = []
        self._items = {}   # detection_key -> DetectionItemWidget
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        header = QGroupBox("Detected Objects")
        header_layout = QHBoxLayout()

        self.count_label = QLabel("0 detections")
        self.count_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        header_layout.addWidget(self.count_label)
        header_layout.addStretch()

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.clear_detections)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: #f44336; color: white; border: none;
                padding: 5px 10px; border-radius: 3px;
            }
            QPushButton:hover { background: #d32f2f; }
        """)
        header_layout.addWidget(clear_btn)
        header.setLayout(header_layout)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.list_layout.setSpacing(5)

        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll)

    def add_detection(self, detection_data):
        """Add a new detection, or update the existing one in place (upsert)."""
        key = detection_key(detection_data)
        existing = self._items.get(key)
        if existing is not None:
            existing.set_data(detection_data)   # e.g. depth arrived -> show distance
            return

        item = DetectionItemWidget(detection_data)
        self._items[key] = item
        self.detections.append(detection_data)
        self.list_layout.insertWidget(0, item)   # newest first

        n = len(self._items)
        self.count_label.setText(f"{n} detection{'s' if n != 1 else ''}")

    def clear_detections(self):
        """Remove all detections"""
        self.detections = []
        self._items = {}
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.count_label.setText("0 detections")

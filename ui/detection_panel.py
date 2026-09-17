"""Detection panel - scrollable list of detected objects with thumbnails"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QFrame, QPushButton, QGroupBox)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPixmap, QImage
import requests
from datetime import datetime

from html import escape as html_escape

from core.checkpoint_client import (checkpoint_distance_m, checkpoint_name)


def detection_key(d):
    """Stable identity shared by a detection's initial + depth POSTs."""
    return d.get("image_file") or d.get("timestamp") or d.get("id")


# Known equipment_info fields, in display order, with their units.
EQUIPMENT_FIELDS = (
    ("category", "Category", ""),
    ("domain", "Domain", ""),
    ("mobility_type", "Mobility", ""),
    ("caliber_mm", "Caliber", " mm"),
    ("max_range_km", "Max range", " km"),
    ("pp_kg", "PP", " kg"),
    ("score", "Score", ""),
)


def equipment_rows(info):
    """Return [(label, value)] for a detection's equipment_info.

    Known fields come first in a fixed order; anything the detector adds
    later still shows up rather than being dropped by a hard-coded list.
    """
    if not isinstance(info, dict):
        return []

    rows = []
    seen = {"name"}
    for key, label, unit in EQUIPMENT_FIELDS:
        seen.add(key)
        value = info.get(key)
        if value is None or value == "":
            continue
        rows.append((label, f"{value}{unit}"))

    for key, value in info.items():
        if key in seen or value is None or value == "":
            continue
        if isinstance(value, (dict, list, tuple)):
            continue
        rows.append((key.replace("_", " ").capitalize(), str(value)))

    return rows


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
        # Thumbnail + core facts on one row, with the equipment and checkpoint
        # blocks stacked underneath so neither is squeezed by the narrow panel.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        outer.setSpacing(3)

        row = QHBoxLayout()

        self._thumb = QLabel()
        self._thumb.setFixedSize(80, 60)
        self._thumb.setStyleSheet("border: 1px solid #ccc; background: #f5f5f5;")
        self._thumb.setScaledContents(True)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._thumb)

        info = QVBoxLayout()
        self._title = QLabel()
        self._dist = QLabel();  self._dist.setStyleSheet("font-size: 11px; color: #666;")
        self._pos = QLabel();   self._pos.setStyleSheet("font-size: 11px; color: #666;")
        self._alt = QLabel();   self._alt.setStyleSheet("font-size: 11px; color: #666;")
        self._time = QLabel();  self._time.setStyleSheet("font-size: 11px; color: #999;")
        for w in (self._title, self._dist, self._pos, self._alt, self._time):
            info.addWidget(w)
        row.addLayout(info, 1)
        outer.addLayout(row)

        # Equipment metadata sent by the detector.
        self._equip = QLabel()
        self._equip.setWordWrap(True)
        self._equip.setTextFormat(Qt.TextFormat.RichText)
        self._equip.setStyleSheet(
            "font-size: 11px; background: #FFF8E1; border-left: 3px solid #FF9800;"
            "padding: 4px 6px;")
        self._equip.setVisible(False)
        outer.addWidget(self._equip)

        # Checkpoints returned by the /nearby lookup for this object.
        self._checkpoints = QLabel()
        self._checkpoints.setWordWrap(True)
        self._checkpoints.setTextFormat(Qt.TextFormat.RichText)
        self._checkpoints.setStyleSheet(
            "font-size: 11px; background: #E0F2F1; border-left: 3px solid #00897B;"
            "padding: 4px 6px;")
        self._checkpoints.setVisible(False)
        outer.addWidget(self._checkpoints)

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

        self._set_equipment(det.get("equipment_info"))

        # Load thumbnail once, when an image_url first appears.
        image_url = det.get("image_url")
        if image_url and image_url != self._loaded_image_url:
            self._load_thumbnail(image_url)

    def _set_equipment(self, info):
        """Show the detector's equipment_info, or hide the block if absent."""
        rows = equipment_rows(info)
        name = (info or {}).get("name") if isinstance(info, dict) else None

        if not rows and not name:
            self._equip.clear()
            self._equip.setVisible(False)
            return

        html = (f"<b style='color:#E65100;'>🛠 "
                f"{html_escape(str(name or 'Equipment'))}</b>")
        if rows:
            html += "<table cellspacing='0' cellpadding='0' width='100%'>"
            for label, value in rows:
                html += (f"<tr>"
                         f"<td style='color:#777;'>{html_escape(label)}</td>"
                         f"<td style='color:#222;' align='right'>"
                         f"{html_escape(value)}</td>"
                         f"</tr>")
            html += "</table>"

        self._equip.setText(html)
        self._equip.setVisible(True)

    def set_checkpoints(self, data):
        """Show the /nearby result for this detection.

        ``data`` is the dict the lookup emits. ``failed`` marks a lookup that
        did not come back, so a silent miss is distinguishable from a genuine
        "none nearby".
        """
        if not data:
            self._checkpoints.clear()
            self._checkpoints.setVisible(False)
            return

        radius_km = (data.get("radius_m") or 0.0) / 1000.0
        label = data.get("equipment_name") or data.get("object_class") or "object"

        if data.get("failed"):
            # A slow service and a missing one need different fixes, so say
            # which happened rather than only "failed".
            headline, hint = {
                "timeout": ("Checkpoints: no answer in time",
                            "the service is reachable but slow - raise "
                            "CHECKPOINT_API_TIMEOUT"),
                "unreachable": ("Checkpoints: service unreachable",
                                "check the address, port, and that it is "
                                "running"),
                "http": ("Checkpoints: service returned an error",
                         "see the console for the status code"),
                "bad_json": ("Checkpoints: reply was not JSON",
                             "see the console for what came back"),
            }.get(data.get("error_kind"),
                  ("Checkpoint lookup failed", "see the console for details"))

            html = (f"<b style='color:#C62828;'>🛡 {html_escape(headline)}</b>"
                    f"<br/><span style='color:#777;'>{html_escape(hint)}"
                    f"</span>")
            self._checkpoints.setText(html)
            self._checkpoints.setVisible(True)
            return

        checkpoints = data.get("checkpoints") or []
        count = data.get("checkpoint_count", len(checkpoints))

        html = (f"<b style='color:#00695C;'>🛡 {count} checkpoint"
                f"{'' if count == 1 else 's'} within {radius_km:.2f} km</b>")
        html += (f"<br/><span style='color:#777;'>range of "
                 f"{html_escape(str(label))}</span>")

        if checkpoints:
            html += "<table cellspacing='0' cellpadding='0' width='100%'>"
            for checkpoint in checkpoints[:12]:
                name = html_escape(checkpoint_name(checkpoint))
                away = checkpoint_distance_m(checkpoint)
                away_text = f"{away:,.0f} m" if away is not None else ""

                # checkpoint_type / status as the service reports them.
                detail = ""
                if isinstance(checkpoint, dict):
                    kind = (checkpoint.get("checkpoint_type")
                            or checkpoint.get("type")
                            or checkpoint.get("category"))
                    state = checkpoint.get("status")
                    bits = [str(b) for b in (kind, state) if b]
                    if bits:
                        detail = (f"<br/><span style='color:#999;'>&nbsp;&nbsp;"
                                  f"{html_escape(' · '.join(bits))}</span>")

                html += (f"<tr><td style='color:#222;'>• {name}{detail}</td>"
                         f"<td style='color:#777;' align='right' "
                         f"valign='top'>{away_text}</td></tr>")
            html += "</table>"
            if len(checkpoints) > 12:
                html += (f"<span style='color:#777;'>+ "
                         f"{len(checkpoints) - 12} more</span>")

        self._checkpoints.setText(html)
        self._checkpoints.setVisible(True)

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

    def set_checkpoints(self, key, data):
        """Route a /nearby result to the detection item it belongs to."""
        item = self._items.get(key)
        if item is not None:
            item.set_checkpoints(data)

    def clear_detections(self):
        """Remove all detections"""
        self.detections = []
        self._items = {}
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.count_label.setText("0 detections")

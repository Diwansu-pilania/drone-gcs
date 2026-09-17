"""Detection panel - scrollable list of detected objects with thumbnails"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QFrame, QPushButton, QGroupBox)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap, QImage
import requests
from datetime import datetime

import config

from html import escape as html_escape

from core.checkpoint_client import (checkpoint_distance_m, checkpoint_name)
from core.threat_score import score_detection
from ui import theme


class ClickableWidget(QWidget):
    """A plain widget that reports a click.

    The whole header row is the toggle: a small chevron alone would be a
    needlessly precise target in a narrow panel.
    """

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


BAND_COLOURS = theme.BAND_COLOURS
BAND_COLOUR_DEFAULT = theme.BAND_DEFAULT


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

    # (detection, authorized_by, drone) — raised when the button is clicked.
    response_authorized = pyqtSignal(dict, str, dict)

    def __init__(self, detection):
        super().__init__()
        self._thumb = None
        self._loaded_image_url = None
        self._selected_drone = None
        self._authorized_by = None
        self._expanded = False
        # What the closed row shows, kept as values rather than parsed back
        # out of the rich text below.
        self._threat_summary = None
        self._drone_summary = None
        self._checkpoint_summary = None
        self._init_ui()
        self.set_data(detection)
        # Each detection is a card, so the list can be scanned rather than
        # read. Two things are needed for that surface to actually appear:
        # a QWidget subclass paints no stylesheet background unless told to,
        # and the children must not paint the window background over it. The
        # detail sections keep their own surface regardless, since a widget's
        # own stylesheet wins over an ancestor's.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            DetectionItemWidget {{
                background: {theme.SURFACE};
                border: 1px solid {theme.BORDER};
                border-radius: 3px;
            }}
            DetectionItemWidget:hover {{
                border: 1px solid {theme.BORDER_STRONG};
            }}
            DetectionItemWidget QWidget {{
                background: transparent;
            }}
        """)

    def _init_ui(self):
        # Two parts: a header that is always visible, and the detail below it
        # that collapses. Every detection carries a threat score, equipment,
        # a response drone and its checkpoints, which is far too much to show
        # for all of them at once in a narrow panel — so the header carries
        # the summary and the rest opens on a click.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        outer.setSpacing(3)

        self._header = ClickableWidget()
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self.toggle)
        row = QHBoxLayout(self._header)
        row.setContentsMargins(0, 0, 0, 0)

        self._thumb = QLabel()
        self._thumb.setFixedSize(80, 60)
        self._thumb.setStyleSheet(
            f"border: 1px solid {theme.BORDER_STRONG}; background: {theme.BG};")
        self._thumb.setScaledContents(True)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._thumb)

        info = QVBoxLayout()
        self._title = QLabel()
        for name in ("_dist", "_pos", "_alt", "_time"):
            widget = QLabel()
            widget.setTextFormat(Qt.TextFormat.RichText)
            setattr(self, name, widget)
        # The at-a-glance line: what the detail would have said, in one row.
        self._summary = QLabel()
        self._summary.setTextFormat(Qt.TextFormat.RichText)

        # The detection time sits last and right-aligned, against the card's
        # bottom edge: it is how an operator tells a fresh contact from an old
        # one, but it is the least urgent thing on the card. It keeps its own
        # line rather than riding beside the title, because a long class name
        # would otherwise push the card wider than the panel.
        self._time.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)

        for w in (self._title, self._summary, self._dist, self._pos, self._alt,
                  self._time):
            info.addWidget(w)
        row.addLayout(info, 1)

        # Which way this item is about to move, at the right of the header.
        # A caret, not an icon: it points where the item is about to go.
        self._chevron = QLabel("\u203a")
        self._chevron.setStyleSheet(
            f"font-size: 15px; color: {theme.TEXT_MUTED}; font-weight: bold;")
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignTop
                                   | Qt.AlignmentFlag.AlignRight)
        self._chevron.setFixedWidth(14)
        row.addWidget(self._chevron)

        outer.addWidget(self._header)

        # Everything below lives in the collapsing half.
        self._details = QWidget()
        outer_details = QVBoxLayout(self._details)
        outer_details.setContentsMargins(0, 0, 0, 0)
        outer_details.setSpacing(3)
        outer = outer_details          # the blocks below go into the details

        # Threat score: the headline for this detection, so it goes first.
        self._threat = QLabel()
        self._threat.setWordWrap(True)
        self._threat.setTextFormat(Qt.TextFormat.RichText)
        self._threat.setStyleSheet(theme.section_style(theme.TEXT_MUTED))
        self._threat.setVisible(False)
        outer.addWidget(self._threat)

        # Equipment metadata sent by the detector.
        self._equip = QLabel()
        self._equip.setWordWrap(True)
        self._equip.setTextFormat(Qt.TextFormat.RichText)
        self._equip.setStyleSheet(theme.section_style(theme.ACCENT_EQUIPMENT))
        self._equip.setVisible(False)
        outer.addWidget(self._equip)

        # The response drone chosen for this object.
        self._drone = QLabel()
        self._drone.setWordWrap(True)
        self._drone.setTextFormat(Qt.TextFormat.RichText)
        self._drone.setStyleSheet(theme.section_style(theme.ACCENT_RESPONSE))
        self._drone.setVisible(False)
        outer.addWidget(self._drone)

        # Authorisation: records WHO approved this response, in this window.
        # It sends nothing and dispatches nothing — see _on_authorize.
        self._authorize_btn = QPushButton("AUTHORIZE RESPONSE")
        self._authorize_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {theme.ACCENT_RESPONSE};
                           border: 1px solid {theme.ACCENT_RESPONSE};
                           padding: 6px 10px; border-radius: 2px;
                           font-size: {theme.SIZE_SMALL}px; letter-spacing: 1px; }}
            QPushButton:hover {{ background: {theme.ACCENT_RESPONSE};
                                 color: {theme.BG}; }}
            QPushButton:disabled {{ color: {theme.TEXT_MUTED};
                                    border: 1px solid {theme.BORDER}; }}
        """)
        self._authorize_btn.clicked.connect(self._on_authorize)
        self._authorize_btn.setVisible(False)
        outer.addWidget(self._authorize_btn)

        # Checkpoints returned by the /nearby lookup for this object.
        self._checkpoints = QLabel()
        self._checkpoints.setWordWrap(True)
        self._checkpoints.setTextFormat(Qt.TextFormat.RichText)
        self._checkpoints.setStyleSheet(theme.section_style(theme.ACCENT_CHECKPOINT))
        self._checkpoints.setVisible(False)
        outer.addWidget(self._checkpoints)

        # Collapsed to begin with: the panel is a list to scan, and an
        # expanded item per detection defeats that.
        self._details.setVisible(False)
        self.layout().addWidget(self._details)

    def _update_summary(self):
        """Rebuild the one-line summary shown while this item is closed.

        Collapsing is only worth doing if the closed row still answers "does
        this need me?" — so the threat, the chosen drone and the checkpoint
        count are carried up here, and the detail below repeats them in full.
        """
        parts = []

        if self._threat_summary:
            score, band = self._threat_summary
            colour = BAND_COLOURS.get(band, BAND_COLOUR_DEFAULT)
            parts.append(theme.value(f"{score:.0f}", size=theme.SIZE_SMALL,
                                     colour=colour, bold=True)
                         + theme.value("/100", size=theme.SIZE_TINY,
                                       colour=theme.TEXT_MUTED)
                         + "&nbsp;" + theme.chip(band, colour))

        if self._drone_summary:
            parts.append(theme.value(html_escape(str(self._drone_summary).upper()),
                                     size=theme.SIZE_TINY,
                                     colour=theme.ACCENT_RESPONSE))

        if self._checkpoint_summary is not None:
            parts.append(theme.value(f"{self._checkpoint_summary} CP",
                                     size=theme.SIZE_TINY,
                                     colour=theme.ACCENT_CHECKPOINT))

        if self._authorized_by:
            parts.append(theme.chip("authorized", theme.ACCENT_OK))

        separator = theme.value("&nbsp;&nbsp;", size=theme.SIZE_TINY,
                                colour=theme.TEXT_MUTED)
        self._summary.setText(separator.join(parts))
        self._summary.setVisible(bool(parts) and not self._expanded)

    def toggle(self):
        """Open or close this detection's detail."""
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded):
        """Show or hide the detail, and point the chevron accordingly."""
        self._expanded = bool(expanded)
        self._details.setVisible(self._expanded)
        self._chevron.setText("\u2304" if self._expanded else "\u203a")
        self._update_summary()

    def set_data(self, det):
        """Populate/refresh all fields from a (possibly merged) detection dict."""
        self.detection = det

        obj_class = det.get("object_class") or det.get("class") or "unknown"
        title = (f"<span style=\"font-family:{theme.FONT_UI};"
                 f"font-size:{theme.SIZE_TITLE}px;color:{theme.TEXT};"
                 f"font-weight:600;letter-spacing:0.4px;\">"
                 f"{html_escape(str(obj_class).upper())}</span>")
        if det.get("track_id") is not None:
            title += "&nbsp;&nbsp;" + theme.value(
                f"TRK {det['track_id']}", size=theme.SIZE_TINY,
                colour=theme.TEXT_MUTED)
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

        confidence_text = theme.value(
            f"{det.get('confidence', 0.0) * 100:.1f}%", size=theme.SIZE_TINY,
            colour=theme.TEXT_DIM)
        if positive_distance:
            depth_text = theme.value(f"{float(distance):.2f} m",
                                     size=theme.SIZE_TINY, colour=theme.TEXT_DIM)
        elif depth_status == "processing":
            depth_text = theme.value("DEPTH PENDING", size=theme.SIZE_TINY,
                                     colour=theme.TEXT_MUTED)
        elif depth_status == "error":
            depth_text = theme.value("DEPTH ERROR", size=theme.SIZE_TINY,
                                     colour=theme.ACCENT_ALERT)
        else:
            depth_text = ""
        divider = theme.value(" · ", size=theme.SIZE_TINY, colour=theme.TEXT_MUTED)
        self._dist.setText(divider.join(
            part for part in (confidence_text, depth_text) if part))

        # Position (only if the detection carried GPS)
        if det.get("has_gps"):
            position = f"{det['latitude']:.6f}, {det['longitude']:.6f}"
            colour = theme.TEXT_DIM
            if det.get("position_source") == "default_center":
                # An estimate, and it must not read like a surveyed fix.
                position += "  EST"
                colour = theme.BAND_COLOURS["MODERATE"]
            self._pos.setText(theme.value(position, size=theme.SIZE_TINY,
                                          colour=colour))
        else:
            self._pos.setText(theme.value("NO FIX", size=theme.SIZE_TINY,
                                          colour=theme.TEXT_MUTED))

        if det.get("altitude"):
            self._alt.setText(theme.label("alt") + "&nbsp;" + theme.value(
                f"{det['altitude']:.1f} m", size=theme.SIZE_TINY,
                colour=theme.TEXT_DIM))
        else:
            self._alt.setText("")

        self._time.setText(theme.value(
            self._format_time(det.get("timestamp")), size=theme.SIZE_TINY,
            colour=theme.TEXT_MUTED))

        self._set_equipment(det.get("equipment_info"))
        self._set_threat(det)

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

        html = theme.heading("equipment", theme.ACCENT_EQUIPMENT,
                             trailing=theme.value(
                                 html_escape(str(name or "UNKNOWN").upper()),
                                 size=theme.SIZE_SMALL,
                                 colour=theme.ACCENT_EQUIPMENT, bold=True))
        if rows:
            html += theme.rows(rows)

        self._equip.setText(html)
        self._equip.setVisible(True)

    def _set_threat(self, det):
        """Show this detection's threat score out of 100, and what made it."""
        try:
            result = score_detection(det)
        except Exception as exc:        # a scoring slip must not blank the item
            print(f"[threat] could not score detection: {exc}")
            self._threat.clear()
            self._threat.setVisible(False)
            return

        colour = BAND_COLOURS.get(result["band"], BAND_COLOUR_DEFAULT)
        score = result["score"]

        html = theme.heading(
            "threat assessment", theme.TEXT_DIM,
            trailing=theme.value(f"{score:.0f}", size=theme.SIZE_TITLE,
                                 colour=colour, bold=True)
            + theme.value(" / 100", size=theme.SIZE_TINY,
                          colour=theme.TEXT_MUTED)
            + "&nbsp;&nbsp;" + theme.chip(result["band"], colour, filled=True))

        html += theme.bar(score, colour, height=5)

        # Each factor with the weight it carries and the points it earned, so
        # the figure above can be checked rather than taken on faith.
        pairs = []
        for factor in result["factors"]:
            # The weight rides along with the name: it explains the points
            # beside it, and belongs to the label rather than the figure.
            name = f"{factor['label']}  w{factor['weight']:.0f}"
            pairs.append((name, f"{factor['points']:.1f}"
                                if factor["available"] else "--"))
        html += theme.rows(pairs)

        missing = result["factors_total"] - result["factors_present"]
        if missing:
            # A low score from thin data must not read as a low threat.
            html += theme.note(
                f"{missing} of {result['factors_total']} factors unavailable "
                f"&mdash; score is a floor", theme.BAND_COLOURS["MODERATE"])

        self._threat.setText(html)
        self._threat.setVisible(True)
        self._threat_summary = (score, result["band"])
        self._update_summary()

    def set_response_drone(self, result):
        """Show the drone chosen to respond to this object, and why.

        ``result`` is what drone_selection returned. A result with nothing
        feasible is shown too, naming what was ruled out: "no drone can
        respond" is an operational fact, not an empty panel.
        """
        if not result:
            self._drone.clear()
            self._drone.setVisible(False)
            self._selected_drone = None
            self._authorize_btn.setVisible(False)
            return

        selected = result.get("selected")
        excluded = result.get("excluded") or []

        if not selected:
            reason = result.get("unavailable_because")
            html = theme.heading("response", theme.ACCENT_RESPONSE,
                                 trailing=theme.chip("none available",
                                                     theme.TEXT_MUTED))
            if reason:
                html += theme.note(html_escape(str(reason)), theme.TEXT_DIM)
            for entry in excluded[:4]:
                html += theme.note(
                    theme.value(html_escape(
                        str(entry.get("drone_name") or "drone").upper()),
                                size=theme.SIZE_TINY, colour=theme.TEXT_DIM)
                    + " &mdash; "
                    + html_escape("; ".join(entry.get("reasons") or [])))
            if len(excluded) > 4:
                html += theme.note(f"+{len(excluded) - 4} more ruled out")
            self._drone.setText(html)
            self._drone.setVisible(True)
            self._selected_drone = None
            self._authorize_btn.setVisible(False)
            self._drone_summary = None
            self._update_summary()
            return

        name = selected.get("drone_name") or f"drone {selected.get('drone_id')}"
        html = theme.heading("response", theme.ACCENT_RESPONSE,
                             trailing=theme.value(
                                 html_escape(str(name).upper()),
                                 size=theme.SIZE_SMALL,
                                 colour=theme.ACCENT_RESPONSE, bold=True))

        # The figures a dispatch decision turns on, as a column that lines up.
        facts = []
        if selected.get("eta_min") is not None:
            facts.append(("eta", f"{float(selected['eta_min']):.0f} min"))
        if selected.get("distance_km") is not None:
            facts.append(("range", f"{float(selected['distance_km']):.2f} km"))
        if selected.get("battery_percentage") is not None:
            facts.append(("batt", f"{float(selected['battery_percentage']):.0f}%"))
        if selected.get("score") is not None:
            facts.append(("score", f"{float(selected['score']):.0f}"))
        if facts:
            html += theme.rows(facts)

        if selected.get("type_of_drone") or selected.get("status"):
            bits = [str(b).upper() for b in (selected.get("type_of_drone"),
                                             selected.get("status")) if b]
            html += theme.note(" · ".join(html_escape(b) for b in bits))

        why_best = selected.get("why_best") or []
        if why_best:
            html += theme.note(theme.label("why this one"))
            for line in why_best[:3]:
                html += theme.note(html_escape(str(line)), theme.TEXT_DIM)

        others = max(0, len(result.get("candidates") or []) - 1)
        tail = []
        if others:
            tail.append(f"{others} other{'' if others == 1 else 's'} able")
        if excluded:
            tail.append(f"{len(excluded)} ruled out")
        if tail:
            html += theme.note(" · ".join(tail))

        self._drone.setText(html)
        self._drone.setVisible(True)

        self._selected_drone = selected
        self._drone_summary = selected.get("drone_name")
        self._update_summary()
        if self._authorized_by is None:
            self._authorize_btn.setEnabled(True)
            self._authorize_btn.setText("AUTHORIZE RESPONSE")
            self._authorize_btn.setVisible(True)
        else:
            # Already authorised: show that rather than offering it again.
            self._show_authorized()

    def _on_authorize(self):
        """Record who authorised this response.

        This is a record, not a command: nothing is sent and no drone is
        dispatched. It names the person accountable for the decision, in this
        window, and the signal lets the rest of the app act on it later.
        """
        if not self._selected_drone:
            return
        who = getattr(config, "OPERATOR_NAME", "") or "unknown-operator"
        self._authorized_by = who
        self._show_authorized()
        self.response_authorized.emit(self.detection, who,
                                      self._selected_drone)

    def _show_authorized(self):
        """Replace the button with who authorised it, and when."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self._authorize_btn.setEnabled(False)
        self._authorize_btn.setText(
            f"AUTHORIZED  {self._authorized_by}  {stamp}")
        self._authorize_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {theme.ACCENT_OK};
                           border: 1px solid {theme.ACCENT_OK};
                           padding: 6px 10px; border-radius: 2px;
                           font-family: {theme.FONT_MONO};
                           font-size: {theme.SIZE_TINY}px;
                           letter-spacing: 0.6px; }}
            QPushButton:disabled {{ color: {theme.ACCENT_OK};
                                    border: 1px solid {theme.ACCENT_OK}; }}
        """)
        self._authorize_btn.setVisible(True)
        self._update_summary()

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

            html = theme.heading("checkpoints", theme.ACCENT_CHECKPOINT,
                                 trailing=theme.chip("lookup failed",
                                                     theme.ACCENT_ALERT))
            html += theme.note(html_escape(headline), theme.TEXT_DIM)
            html += theme.note(html_escape(hint))
            self._checkpoints.setText(html)
            self._checkpoints.setVisible(True)
            return

        checkpoints = data.get("checkpoints") or []
        count = data.get("checkpoint_count", len(checkpoints))

        html = theme.heading(
            "checkpoints", theme.ACCENT_CHECKPOINT,
            trailing=theme.value(str(count), size=theme.SIZE_SMALL,
                                 colour=theme.ACCENT_CHECKPOINT, bold=True)
            + theme.value(f" within {radius_km:.2f} km", size=theme.SIZE_TINY,
                          colour=theme.TEXT_MUTED))
        html += theme.note(f"range of {html_escape(str(label))}")

        if checkpoints:
            html += "<table cellspacing='0' cellpadding='0' width='100%'>"
            for checkpoint in checkpoints[:12]:
                name = html_escape(checkpoint_name(checkpoint).upper())
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
                        detail = ("<br/>" + theme.label(
                            " · ".join(html_escape(b) for b in bits)))

                html += (f"<tr><td>"
                         f"{theme.value(name, size=theme.SIZE_TINY, colour=theme.TEXT)}"
                         f"{detail}</td>"
                         f"<td align='right' valign='top'>"
                         f"{theme.value(away_text, size=theme.SIZE_TINY, colour=theme.TEXT_DIM)}"
                         f"</td></tr>")
            html += "</table>"
            if len(checkpoints) > 12:
                html += theme.note(f"+{len(checkpoints) - 12} more")

        self._checkpoints.setText(html)
        self._checkpoints.setVisible(True)
        self._checkpoint_summary = count
        self._update_summary()

        # Checkpoints carry weight in the score, so fold the result into the
        # detection and re-score now that the count is known.
        if isinstance(getattr(self, "detection", None), dict):
            self.detection["nearby_checkpoints"] = {
                "status": "failed" if data.get("failed") else "ok",
                "radius_m": data.get("radius_m"),
                "checkpoint_count": count,
                "checkpoints": checkpoints,
            }
            self._set_threat(self.detection)

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

    # (detection, authorized_by, drone) — forwarded from whichever item was
    # authorised.
    response_authorized = pyqtSignal(dict, str, dict)

    def __init__(self):
        super().__init__()
        self.detections = []
        self._items = {}   # detection_key -> DetectionItemWidget
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        header = QGroupBox("DETECTIONS")
        header_layout = QHBoxLayout()

        self.count_label = QLabel()
        self.count_label.setTextFormat(Qt.TextFormat.RichText)
        self._set_count(0)
        header_layout.addWidget(self.count_label)
        header_layout.addStretch()

        self._expand_btn = QPushButton("EXPAND ALL")
        self._expand_btn.setCheckable(True)
        self._expand_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {theme.TEXT_DIM};
                           border: 1px solid {theme.BORDER_STRONG};
                           padding: 5px 9px; border-radius: 2px;
                           font-size: {theme.SIZE_TINY}px; letter-spacing: 0.8px; }}
            QPushButton:hover {{ color: {theme.TEXT};
                                 border: 1px solid {theme.TEXT_MUTED}; }}
            QPushButton:checked {{ color: {theme.TEXT};
                                   background: {theme.SURFACE_HOVER}; }}
        """)
        self._expand_btn.toggled.connect(self._on_expand_all)
        header_layout.addWidget(self._expand_btn)

        clear_btn = QPushButton("CLEAR")
        clear_btn.clicked.connect(self.clear_detections)
        clear_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};
                           border: 1px solid {theme.BORDER_STRONG};
                           padding: 5px 9px; border-radius: 2px;
                           font-size: {theme.SIZE_TINY}px; letter-spacing: 0.8px; }}
            QPushButton:hover {{ color: {theme.ACCENT_ALERT};
                                 border: 1px solid {theme.ACCENT_ALERT}; }}
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
        item.response_authorized.connect(self.response_authorized)
        if self._expand_btn.isChecked():
            item.set_expanded(True)
        self._items[key] = item
        self.detections.append(detection_data)
        self.list_layout.insertWidget(0, item)   # newest first

        self._set_count(len(self._items))

    def set_checkpoints(self, key, data):
        """Route a /nearby result to the detection item it belongs to."""
        item = self._items.get(key)
        if item is not None:
            item.set_checkpoints(data)

    def _set_count(self, n):
        """The tally, with the number carrying the weight rather than the word."""
        self.count_label.setText(
            theme.value(f"{n:02d}", size=theme.SIZE_TITLE,
                        colour=theme.TEXT, bold=True)
            + "&nbsp;" + theme.label("tracked"))

    def _on_expand_all(self, expanded):
        """Open or close every item at once."""
        self._expand_btn.setText("COLLAPSE ALL" if expanded else "EXPAND ALL")
        for item in self._items.values():
            item.set_expanded(expanded)

    def set_response_drone(self, key, result):
        """Route a chosen response drone to its detection item."""
        item = self._items.get(key)
        if item is not None:
            item.set_response_drone(result)

    def clear_detections(self):
        """Remove all detections"""
        self.detections = []
        self._items = {}
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._set_count(0)

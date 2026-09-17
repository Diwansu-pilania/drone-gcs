"""Telemetry panel - displays live vehicle state with gauges and indicators"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QGroupBox, QGridLayout, QFrame)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPolygon
from PyQt6.QtCore import QPoint
import math


class CompassWidget(QWidget):
    """Circular compass showing heading in degrees"""

    def __init__(self):
        super().__init__()
        self.heading = 0.0
        self.setMinimumSize(120, 120)

    def set_heading(self, heading):
        """Update heading (0-360 degrees, 0=North)"""
        self.heading = heading
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Calculate center and radius
        width = self.width()
        height = self.height()
        side = min(width, height)
        center_x = width // 2
        center_y = height // 2
        radius = side // 2 - 10

        # Draw compass circle
        painter.setPen(QPen(QColor("#2196F3"), 2))
        painter.drawEllipse(center_x - radius, center_y - radius,
                           radius * 2, radius * 2)

        # Draw cardinal directions
        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#333"))

        directions = [("N", 0), ("E", 90), ("S", 180), ("W", 270)]
        for label, angle in directions:
            rad = math.radians(angle - 90)
            x = center_x + int((radius - 20) * math.cos(rad))
            y = center_y + int((radius - 20) * math.sin(rad))
            painter.drawText(x - 10, y + 5, label)

        # Draw heading arrow
        painter.setPen(QPen(QColor("#F44336"), 3))
        painter.setBrush(QColor("#F44336"))

        # Convert heading to radians (0° = North = up)
        rad = math.radians(self.heading - 90)
        arrow_length = radius - 30
        end_x = center_x + int(arrow_length * math.cos(rad))
        end_y = center_y + int(arrow_length * math.sin(rad))

        # Draw arrow line
        painter.drawLine(center_x, center_y, end_x, end_y)

        # Draw arrowhead
        arrow_size = 10
        angle1 = rad + math.radians(150)
        angle2 = rad - math.radians(150)
        p1 = QPoint(end_x + int(arrow_size * math.cos(angle1)),
                    end_y + int(arrow_size * math.sin(angle1)))
        p2 = QPoint(end_x + int(arrow_size * math.cos(angle2)),
                    end_y + int(arrow_size * math.sin(angle2)))
        polygon = QPolygon([QPoint(end_x, end_y), p1, p2])
        painter.drawPolygon(polygon)

        # Draw heading text
        painter.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        painter.setPen(QColor("#000"))
        heading_text = f"{int(self.heading)}°"
        painter.drawText(center_x - 20, center_y + radius + 20, heading_text)


class TelemetryPanel(QWidget):
    """Panel displaying all vehicle telemetry data"""

    def __init__(self, vehicle_state):
        super().__init__()
        self.vehicle_state = vehicle_state
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Connection status
        self.status_label = QLabel("⚫ Disconnected")
        self.status_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                padding: 5px;
                background-color: #f44336;
                color: white;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.status_label)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout()
        self.lat_label = self._create_value_label("0.000000")
        self.lon_label = self._create_value_label("0.000000")
        self.alt_label = self._create_value_label("0.0 m")
        self.rel_alt_label = self._create_value_label("0.0 m")

        pos_layout.addWidget(QLabel("Latitude:"), 0, 0)
        pos_layout.addWidget(self.lat_label, 0, 1)
        pos_layout.addWidget(QLabel("Longitude:"), 1, 0)
        pos_layout.addWidget(self.lon_label, 1, 1)
        pos_layout.addWidget(QLabel("Altitude (MSL):"), 2, 0)
        pos_layout.addWidget(self.alt_label, 2, 1)
        pos_layout.addWidget(QLabel("Altitude (Rel):"), 3, 0)
        pos_layout.addWidget(self.rel_alt_label, 3, 1)
        pos_group.setLayout(pos_layout)
        layout.addWidget(pos_group)

        # Attitude & Heading group
        att_group = QGroupBox("Attitude & Heading")
        att_layout = QHBoxLayout()

        # Compass
        self.compass = CompassWidget()
        att_layout.addWidget(self.compass)

        # Roll/Pitch values
        rp_layout = QGridLayout()
        self.roll_label = self._create_value_label("0.0°")
        self.pitch_label = self._create_value_label("0.0°")
        self.yaw_label = self._create_value_label("0.0°")

        rp_layout.addWidget(QLabel("Roll:"), 0, 0)
        rp_layout.addWidget(self.roll_label, 0, 1)
        rp_layout.addWidget(QLabel("Pitch:"), 1, 0)
        rp_layout.addWidget(self.pitch_label, 1, 1)
        rp_layout.addWidget(QLabel("Yaw:"), 2, 0)
        rp_layout.addWidget(self.yaw_label, 2, 1)

        att_layout.addLayout(rp_layout)
        att_group.setLayout(att_layout)
        layout.addWidget(att_group)

        # Speed group
        speed_group = QGroupBox("Speed")
        speed_layout = QGridLayout()
        self.airspeed_label = self._create_value_label("0.0 m/s")
        self.groundspeed_label = self._create_value_label("0.0 m/s")

        speed_layout.addWidget(QLabel("Airspeed:"), 0, 0)
        speed_layout.addWidget(self.airspeed_label, 0, 1)
        speed_layout.addWidget(QLabel("Groundspeed:"), 1, 0)
        speed_layout.addWidget(self.groundspeed_label, 1, 1)
        speed_group.setLayout(speed_layout)
        layout.addWidget(speed_group)

        # Battery group
        battery_group = QGroupBox("Battery")
        battery_layout = QGridLayout()
        self.voltage_label = self._create_value_label("0.0 V")
        self.remaining_label = self._create_value_label("0%")
        self.current_label = self._create_value_label("0.0 A")

        battery_layout.addWidget(QLabel("Voltage:"), 0, 0)
        battery_layout.addWidget(self.voltage_label, 0, 1)
        battery_layout.addWidget(QLabel("Remaining:"), 1, 0)
        battery_layout.addWidget(self.remaining_label, 1, 1)
        battery_layout.addWidget(QLabel("Current:"), 2, 0)
        battery_layout.addWidget(self.current_label, 2, 1)
        battery_group.setLayout(battery_layout)
        layout.addWidget(battery_group)

        # GPS group
        gps_group = QGroupBox("GPS")
        gps_layout = QGridLayout()
        self.gps_fix_label = self._create_value_label("No GPS")
        self.satellites_label = self._create_value_label("0")

        gps_layout.addWidget(QLabel("Fix Type:"), 0, 0)
        gps_layout.addWidget(self.gps_fix_label, 0, 1)
        gps_layout.addWidget(QLabel("Satellites:"), 1, 0)
        gps_layout.addWidget(self.satellites_label, 1, 1)
        gps_group.setLayout(gps_layout)
        layout.addWidget(gps_group)

        # Flight status group
        status_group = QGroupBox("Flight Status")
        status_layout = QGridLayout()
        self.mode_label = self._create_value_label("UNKNOWN")
        self.armed_label = self._create_value_label("DISARMED")

        status_layout.addWidget(QLabel("Mode:"), 0, 0)
        status_layout.addWidget(self.mode_label, 0, 1)
        status_layout.addWidget(QLabel("Armed:"), 1, 0)
        status_layout.addWidget(self.armed_label, 1, 1)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        layout.addStretch()

    def _create_value_label(self, text):
        """Create a styled label for displaying values"""
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; color: #2196F3;")
        return label

    def _connect_signals(self):
        """Connect vehicle state signals to update methods"""
        self.vehicle_state.position_changed.connect(self.update_position)
        self.vehicle_state.attitude_changed.connect(self.update_attitude)
        self.vehicle_state.heading_changed.connect(self.update_heading)
        self.vehicle_state.battery_changed.connect(self.update_battery)
        self.vehicle_state.gps_changed.connect(self.update_gps)
        self.vehicle_state.mode_changed.connect(self.update_mode)
        self.vehicle_state.armed_changed.connect(self.update_armed)
        self.vehicle_state.airspeed_changed.connect(self.update_airspeed)
        self.vehicle_state.groundspeed_changed.connect(self.update_groundspeed)

    @pyqtSlot(float, float, float)
    def update_position(self, lat, lon, alt):
        """Update position display"""
        self.lat_label.setText(f"{lat:.6f}")
        self.lon_label.setText(f"{lon:.6f}")
        self.alt_label.setText(f"{alt:.1f} m")
        self.rel_alt_label.setText(f"{self.vehicle_state.relative_altitude:.1f} m")

        # Update connection status
        if self.vehicle_state.get_connection_status():
            self.status_label.setText("🟢 Connected")
            self.status_label.setStyleSheet("""
                QLabel {
                    font-size: 14px;
                    font-weight: bold;
                    padding: 5px;
                    background-color: #4CAF50;
                    color: white;
                    border-radius: 3px;
                }
            """)

    @pyqtSlot(float, float, float)
    def update_attitude(self, roll, pitch, yaw):
        """Update attitude display"""
        self.roll_label.setText(f"{math.degrees(roll):.1f}°")
        self.pitch_label.setText(f"{math.degrees(pitch):.1f}°")
        self.yaw_label.setText(f"{math.degrees(yaw):.1f}°")

    @pyqtSlot(float)
    def update_heading(self, heading):
        """Update compass heading"""
        self.compass.set_heading(heading)

    @pyqtSlot(float, float)
    def update_battery(self, voltage, remaining):
        """Update battery display"""
        self.voltage_label.setText(f"{voltage:.1f} V")
        self.remaining_label.setText(f"{remaining:.0f}%")
        self.current_label.setText(f"{self.vehicle_state.battery_current:.1f} A")

        # Color code remaining percentage
        if remaining < 20:
            color = "#f44336"  # Red
        elif remaining < 50:
            color = "#FF9800"  # Orange
        else:
            color = "#4CAF50"  # Green
        self.remaining_label.setStyleSheet(f"font-weight: bold; color: {color};")

    @pyqtSlot(int, int)
    def update_gps(self, fix_type, satellites):
        """Update GPS display"""
        fix_names = ["No GPS", "No Fix", "2D Fix", "3D Fix"]
        fix_name = fix_names[fix_type] if fix_type < len(fix_names) else "Unknown"
        self.gps_fix_label.setText(fix_name)
        self.satellites_label.setText(str(satellites))

        # Color code GPS fix
        if fix_type >= 3:
            color = "#4CAF50"  # Green for 3D fix
        elif fix_type == 2:
            color = "#FF9800"  # Orange for 2D fix
        else:
            color = "#f44336"  # Red for no fix
        self.gps_fix_label.setStyleSheet(f"font-weight: bold; color: {color};")

    @pyqtSlot(str)
    def update_mode(self, mode):
        """Update flight mode display"""
        self.mode_label.setText(mode)

    @pyqtSlot(bool)
    def update_armed(self, armed):
        """Update armed status display"""
        if armed:
            self.armed_label.setText("ARMED")
            self.armed_label.setStyleSheet("font-weight: bold; color: #f44336;")
        else:
            self.armed_label.setText("DISARMED")
            self.armed_label.setStyleSheet("font-weight: bold; color: #4CAF50;")

    @pyqtSlot(float)
    def update_airspeed(self, speed):
        """Update airspeed display"""
        self.airspeed_label.setText(f"{speed:.1f} m/s")

    @pyqtSlot(float)
    def update_groundspeed(self, speed):
        """Update groundspeed display"""
        self.groundspeed_label.setText(f"{speed:.1f} m/s")

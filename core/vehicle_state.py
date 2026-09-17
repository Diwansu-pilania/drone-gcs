"""Vehicle state model - holds all telemetry data"""
from PyQt6.QtCore import QObject, pyqtSignal
from datetime import datetime


class VehicleState(QObject):
    """Holds complete vehicle state with Qt signals for UI updates"""

    # Signals emitted when state changes
    position_changed = pyqtSignal(float, float, float)  # lat, lon, alt
    attitude_changed = pyqtSignal(float, float, float)  # roll, pitch, yaw
    heading_changed = pyqtSignal(float)  # compass heading in degrees
    velocity_changed = pyqtSignal(float, float, float)  # vx, vy, vz
    battery_changed = pyqtSignal(float, float)  # voltage, remaining %
    gps_changed = pyqtSignal(int, int)  # fix_type, satellites_visible
    mode_changed = pyqtSignal(str)  # flight mode
    armed_changed = pyqtSignal(bool)  # armed status
    airspeed_changed = pyqtSignal(float)  # m/s
    groundspeed_changed = pyqtSignal(float)  # m/s

    def __init__(self):
        super().__init__()

        # Position (GPS)
        self.latitude = 0.0
        self.longitude = 0.0
        self.altitude = 0.0  # MSL in meters
        self.relative_altitude = 0.0  # relative to home

        # Attitude
        self.roll = 0.0  # radians
        self.pitch = 0.0  # radians
        self.yaw = 0.0  # radians
        self.heading = 0.0  # compass heading in degrees (0-360)

        # Velocity
        self.vx = 0.0  # m/s
        self.vy = 0.0  # m/s
        self.vz = 0.0  # m/s

        # Battery
        self.battery_voltage = 0.0  # volts
        self.battery_remaining = 0.0  # percentage
        self.battery_current = 0.0  # amps

        # GPS
        self.gps_fix_type = 0  # 0=No GPS, 1=No Fix, 2=2D Fix, 3=3D Fix
        self.satellites_visible = 0

        # Flight status
        self.mode = "UNKNOWN"
        self.armed = False
        self.system_status = 0

        # Speed
        self.airspeed = 0.0  # m/s
        self.groundspeed = 0.0  # m/s

        # Timestamps
        self.last_heartbeat = None
        self.last_position_update = None

        # Flight path for map
        self.flight_path = []  # list of (lat, lon, timestamp)

    def update_position(self, lat, lon, alt, relative_alt=None):
        """Update GPS position"""
        self.latitude = lat
        self.longitude = lon
        self.altitude = alt
        if relative_alt is not None:
            self.relative_altitude = relative_alt

        self.last_position_update = datetime.now()

        # Add to flight path. Keep every point for the whole session, from
        # the moment the link is established until it ends.
        self.flight_path.append((lat, lon, self.last_position_update))

        self.position_changed.emit(lat, lon, alt)

    def update_attitude(self, roll, pitch, yaw):
        """Update attitude (radians)"""
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw

        # Convert yaw to compass heading (0-360 degrees)
        import math
        heading_deg = math.degrees(yaw)
        # Normalize to 0-360
        self.heading = (heading_deg + 360) % 360

        self.attitude_changed.emit(roll, pitch, yaw)
        self.heading_changed.emit(self.heading)

    def update_velocity(self, vx, vy, vz):
        """Update velocity in m/s"""
        self.vx = vx
        self.vy = vy
        self.vz = vz
        self.velocity_changed.emit(vx, vy, vz)

    def update_battery(self, voltage, remaining, current=None):
        """Update battery status"""
        self.battery_voltage = voltage / 1000.0  # mV to V
        self.battery_remaining = remaining
        if current is not None:
            self.battery_current = current / 100.0  # cA to A
        self.battery_changed.emit(self.battery_voltage, self.battery_remaining)

    def update_gps(self, fix_type, satellites):
        """Update GPS status"""
        self.gps_fix_type = fix_type
        self.satellites_visible = satellites
        self.gps_changed.emit(fix_type, satellites)

    def update_mode(self, mode_name):
        """Update flight mode"""
        self.mode = mode_name
        self.mode_changed.emit(mode_name)

    def update_armed(self, armed):
        """Update armed status"""
        self.armed = armed
        self.armed_changed.emit(armed)

    def update_airspeed(self, speed):
        """Update airspeed in m/s"""
        self.airspeed = speed
        self.airspeed_changed.emit(speed)

    def update_groundspeed(self, speed):
        """Update groundspeed in m/s"""
        self.groundspeed = speed
        self.groundspeed_changed.emit(speed)

    def update_heartbeat(self):
        """Record heartbeat received"""
        self.last_heartbeat = datetime.now()

    def get_connection_status(self):
        """Check if vehicle is connected (heartbeat within last 3 seconds)"""
        if self.last_heartbeat is None:
            return False
        elapsed = (datetime.now() - self.last_heartbeat).total_seconds()
        return elapsed < 3.0

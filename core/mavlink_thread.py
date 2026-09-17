"""MAVLink connection thread - connects to vehicle and updates state"""
from PyQt6.QtCore import QThread, pyqtSignal
from pymavlink import mavutil
import time
import math


class MAVLinkThread(QThread):
    """Background thread that handles MAVLink communication"""

    # Signals for connection status
    connection_established = pyqtSignal()
    connection_lost = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, vehicle_state, connection_string, baud_rate=57600):
        super().__init__()
        self.vehicle_state = vehicle_state
        self.connection_string = connection_string
        self.baud_rate = baud_rate
        self.running = False
        self.master = None

    def run(self):
        """Main thread loop - connects and processes MAVLink messages"""
        self.running = True

        try:
            # Connect to vehicle
            print(f"Connecting to {self.connection_string}...")
            if self.connection_string.startswith("COM") or self.connection_string.startswith("/dev/"):
                # Serial connection
                self.master = mavutil.mavlink_connection(
                    self.connection_string,
                    baud=self.baud_rate
                )
            else:
                # UDP/TCP connection
                self.master = mavutil.mavlink_connection(self.connection_string)

            # Wait for first heartbeat
            print("Waiting for heartbeat...")
            self.master.wait_heartbeat()
            print(f"Connected to system {self.master.target_system}, component {self.master.target_component}")
            self.connection_established.emit()

            # Ask the autopilot to stream telemetry. Without this, ArduPilot
            # only sends HEARTBEAT. We use both mechanisms for compatibility:
            #   * REQUEST_DATA_STREAM  (older firmware / SITL)
            #   * SET_MESSAGE_INTERVAL (modern ArduPilot per-message)
            self._request_data_streams()

            # Main message processing loop
            while self.running:
                msg = self.master.recv_match(blocking=True, timeout=1.0)

                if msg is None:
                    # Check for connection timeout
                    if not self.vehicle_state.get_connection_status():
                        self.connection_lost.emit()
                    continue

                # Process message by type
                msg_type = msg.get_type()

                if msg_type == "HEARTBEAT":
                    self._handle_heartbeat(msg)

                elif msg_type == "GLOBAL_POSITION_INT":
                    self._handle_global_position(msg)

                elif msg_type == "ATTITUDE":
                    self._handle_attitude(msg)

                elif msg_type == "GPS_RAW_INT":
                    self._handle_gps_raw(msg)

                elif msg_type == "SYS_STATUS":
                    self._handle_sys_status(msg)

                elif msg_type == "VFR_HUD":
                    self._handle_vfr_hud(msg)

        except Exception as e:
            error_msg = f"MAVLink error: {str(e)}"
            print(error_msg)
            self.error_occurred.emit(error_msg)

        finally:
            if self.master:
                self.master.close()

    def _request_data_streams(self):
        """Request telemetry streams from the autopilot."""
        try:
            # Legacy: request ALL streams at 4 Hz
            self.master.mav.request_data_stream_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                4,   # rate Hz
                1    # start
            )

            # Modern: per-message intervals (msg_id -> rate Hz)
            wanted = {
                mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 5,
                mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 8,
                mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 2,
                mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 2,
                mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD: 4,
            }
            for msg_id, hz in wanted.items():
                interval_us = int(1e6 / hz)
                self.master.mav.command_long_send(
                    self.master.target_system,
                    self.master.target_component,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0,
                    msg_id, interval_us, 0, 0, 0, 0, 0
                )
            print("Data stream requests sent.")
        except Exception as e:
            print(f"Failed to request data streams: {e}")

    def _handle_heartbeat(self, msg):
        """Process HEARTBEAT message"""
        self.vehicle_state.update_heartbeat()

        # Update armed status (check MAV_STATE)
        # MAV_STATE_ACTIVE = 4 means armed
        armed = (msg.system_status == 4)
        self.vehicle_state.update_armed(armed)

        # Get flight mode name
        mode_name = self._get_mode_name(msg.custom_mode, msg.type)
        self.vehicle_state.update_mode(mode_name)

    def _handle_global_position(self, msg):
        """Process GLOBAL_POSITION_INT message"""
        # Convert from 1E7 format to degrees
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt_msl = msg.alt / 1000.0  # mm to meters
        alt_rel = msg.relative_alt / 1000.0  # mm to meters

        self.vehicle_state.update_position(lat, lon, alt_msl, alt_rel)

        # Update velocity (cm/s to m/s)
        vx = msg.vx / 100.0
        vy = msg.vy / 100.0
        vz = msg.vz / 100.0
        self.vehicle_state.update_velocity(vx, vy, vz)

        # Update heading from this message (cdeg to deg)
        if msg.hdg != 65535:  # 65535 means unknown
            heading = msg.hdg / 100.0
            self.vehicle_state.heading = heading
            self.vehicle_state.heading_changed.emit(heading)

    def _handle_attitude(self, msg):
        """Process ATTITUDE message"""
        self.vehicle_state.update_attitude(msg.roll, msg.pitch, msg.yaw)

    def _handle_gps_raw(self, msg):
        """Process GPS_RAW_INT message"""
        # GPS fix type: 0=No GPS, 1=No Fix, 2=2D, 3=3D
        self.vehicle_state.update_gps(msg.fix_type, msg.satellites_visible)

    def _handle_sys_status(self, msg):
        """Process SYS_STATUS message"""
        # Battery voltage in mV, remaining in %
        voltage = msg.voltage_battery
        remaining = msg.battery_remaining
        current = msg.current_battery if hasattr(msg, 'current_battery') else None

        self.vehicle_state.update_battery(voltage, remaining, current)

    def _handle_vfr_hud(self, msg):
        """Process VFR_HUD message - airspeed, groundspeed, etc"""
        self.vehicle_state.update_airspeed(msg.airspeed)
        self.vehicle_state.update_groundspeed(msg.groundspeed)

    def _get_mode_name(self, custom_mode, mav_type):
        """Convert custom_mode to readable flight mode name"""
        # ArduPilot mode mappings (simplified)
        copter_modes = {
            0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO",
            4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
            9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
            15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW",
            19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL",
            22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID",
            26: "AUTOROTATE", 27: "AUTO_RTL"
        }

        plane_modes = {
            0: "MANUAL", 1: "CIRCLE", 2: "STABILIZE", 3: "TRAINING",
            4: "ACRO", 5: "FLY_BY_WIRE_A", 6: "FLY_BY_WIRE_B",
            7: "CRUISE", 8: "AUTOTUNE", 10: "AUTO", 11: "RTL",
            12: "LOITER", 15: "GUIDED", 16: "INITIALISING",
            17: "QSTABILIZE", 18: "QHOVER", 19: "QLOITER",
            20: "QLAND", 21: "QRTL", 22: "QAUTOTUNE", 23: "QACRO"
        }

        rover_modes = {
            0: "MANUAL", 1: "ACRO", 3: "STEERING", 4: "HOLD",
            5: "LOITER", 10: "AUTO", 11: "RTL", 12: "SMART_RTL",
            15: "GUIDED", 16: "INITIALISING"
        }

        # MAV_TYPE values
        MAV_TYPE_QUADROTOR = 2
        MAV_TYPE_FIXED_WING = 1
        MAV_TYPE_GROUND_ROVER = 10

        if mav_type == MAV_TYPE_QUADROTOR:
            return copter_modes.get(custom_mode, f"MODE_{custom_mode}")
        elif mav_type == MAV_TYPE_FIXED_WING:
            return plane_modes.get(custom_mode, f"MODE_{custom_mode}")
        elif mav_type == MAV_TYPE_GROUND_ROVER:
            return rover_modes.get(custom_mode, f"MODE_{custom_mode}")
        else:
            return f"MODE_{custom_mode}"

    def stop(self):
        """Stop the thread"""
        self.running = False
        self.wait()

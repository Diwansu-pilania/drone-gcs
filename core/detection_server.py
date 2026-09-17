"""Detection API server - receives object detections via HTTP and forwards to UI

A detection arrives as TWO POSTs from the detector (see the FastAPI client):

  1. INITIAL  - multipart/form-data: an `image` file part + a `metadata` (or
                `detection`) form field holding the JSON below. depth is still
                {"status": "processing"}.
  2. DEPTH    - application/json: the same JSON, now with the completed
                {"status": "complete", "distance_m": ...}. No image.

Both carry the same `image_file` (and `timestamp`), so this server MERGES them
into ONE record keyed by `image_file` (falling back to `timestamp`). The record
keeps a single stable `id` across both POSTs - it is NOT re-numbered, and it is
independent of the detector's own tracker id.

  JSON payload:
  {
    "tracker_id": 1,                    # `track_id` is also accepted
    "class": "car",
    "confidence": 0.4044,
    "bbox": {"x1": 506, "y1": 1541, "x2": 557, "y2": 1600},
    "timestamp": 1786350413,            # unix epoch seconds (or ISO string)
    "datetime": "2026-08-10 13:56:53",
    "image_file": "car_id1_1786350413.jpg",
    "gps": null,                        # null, {"lat":..,"lon":..} or [lat, lon]
    "altitude": null,
    "depth": {"status": "complete", "distance_m": 8.7858}
  }

The metadata JSON may also be delivered as the `detection`/`data`/`json` form
field, or inline in an application/json body with an optional `image_base64`.
Older payloads (object_class/latitude/longitude/heading) are still accepted.

The server binds to 0.0.0.0 so Tailscale/LAN peers can POST to
http://<this-node-tailscale-ip>:5000/detection. Each merged record is saved to
detection_records/<image_file>.json and appended to detections.jsonl.
"""
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from PyQt6.QtCore import QObject, pyqtSignal
import threading
import base64
import json
import os
import socket
import subprocess
from datetime import datetime

from core.tile_server import guess_image_mime
from core.detection_flow import should_replace_depth


class DetectionServer(QObject):
    """Flask server running in a background thread; emits a Qt signal per detection."""

    detection_received = pyqtSignal(dict)  # emits detection dict to the UI

    def __init__(self, host, port, image_dir, tile_cache=None, bind_host=None,
                 log_file=None):
        super().__init__()
        self.host = host
        # What the socket actually binds to. Default 0.0.0.0 so remote peers
        # (Tailscale/LAN) can reach it; `host` is only used to build local URLs.
        self.bind_host = bind_host or "0.0.0.0"
        self.port = port

        # Absolute so saving and serving (send_from_directory) agree on the
        # folder regardless of the Flask app's root_path / the process CWD.
        self.image_dir = os.path.abspath(image_dir)
        os.makedirs(self.image_dir, exist_ok=True)

        base = os.path.dirname(self.image_dir)
        # One JSON file per detection (merged latest state), keyed by image_file.
        self.record_dir = os.path.join(base, "detection_records")
        os.makedirs(self.record_dir, exist_ok=True)
        # Append-only raw event log (one line per POST).
        self.log_file = log_file or os.path.join(base, "detections.jsonl")
        self._file_lock = threading.Lock()

        # Optional offline map tile cache (core.tile_server.TileCache)
        self.tile_cache = tile_cache

        self.detections = []        # merged records, in arrival order
        self._index = {}            # merge-key -> record (same objects as above)
        self._merge_lock = threading.Lock()
        self._counter = 0

        self.app = Flask(__name__)
        CORS(self.app)
        self._register_routes()
        self._thread = None

    def _register_routes(self):
        app = self.app

        @app.route("/detection", methods=["POST"])
        def receive_detection():
            try:
                detection = self._handle_post()
                # Emit a copy so later merges can't mutate what the UI is reading.
                self.detection_received.emit(dict(detection))
                return jsonify({
                    "status": "ok",
                    "id": detection["id"],
                    "image_file": detection.get("image_file"),
                    "depth_status": (detection.get("depth") or {}).get("status"),
                }), 200
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 400

        @app.route("/detections", methods=["GET"])
        def list_detections():
            return jsonify(self.detections), 200

        @app.route("/images/<path:filename>", methods=["GET"])
        def get_image(filename):
            return send_from_directory(self.image_dir, filename)

        @app.route("/tiles/<layer>/<int:z>/<int:x>/<int:y>.png", methods=["GET"])
        def get_tile(layer, z, x, y):
            """Serve a map tile for one layer from the offline cache."""
            if self.tile_cache is None:
                return b"", 404
            data, _is_blank = self.tile_cache.get_tile(layer, z, x, y)
            return Response(data, mimetype=guess_image_mime(data))

        @app.route("/tiles/<int:z>/<int:x>/<int:y>.png", methods=["GET"])
        def get_tile_legacy(z, x, y):
            """Back-compat: no layer name -> serve the default base layer."""
            if self.tile_cache is None:
                return b"", 404
            data, _is_blank = self.tile_cache.get_tile(
                self.tile_cache.default_base, z, x, y)
            return Response(data, mimetype=guess_image_mime(data))

        @app.route("/health", methods=["GET"])
        def health():
            tiles = self.tile_cache.cached_tile_count() if self.tile_cache else 0
            return jsonify({
                "status": "running",
                "count": len(self.detections),
                "cached_tiles": tiles,
            }), 200

    # ------------------------------------------------------------------ #
    # Request handling: parse -> merge -> persist
    # ------------------------------------------------------------------ #
    def _handle_post(self):
        data, image_bytes, image_name = self._extract_payload_and_image()

        # Save the image under the SENDER's own filename so both POSTs and the
        # UI reference the exact same file (no synthetic id prefix).
        image_url = None
        if image_bytes:
            saved = self._save_image(image_bytes, image_name)
            image_name = image_name or saved
            image_url = f"http://{self.host}:{self.port}/images/{saved}"

        normalized = self._normalize(data, image_name, image_url)
        key = self._merge_key(data, image_name)
        detection = self._merge(normalized, key)
        self._persist(detection, key)
        return detection

    def _extract_payload_and_image(self):
        """Return (metadata_dict, image_bytes_or_None, image_name_or_None)."""
        ctype = request.content_type or ""

        if "multipart/form-data" in ctype:
            # Metadata JSON may be under any of these field names.
            raw = (request.form.get("metadata")
                   or request.form.get("detection")
                   or request.form.get("data")
                   or request.form.get("json"))
            data = json.loads(raw) if raw else self._form_to_dict(request.form)

            image_file = (request.files.get("image")
                          or request.files.get("file")
                          or request.files.get("frame"))
            image_bytes = image_file.read() if image_file else None
            image_name = (image_file.filename if image_file
                          else data.get("image_file"))
            return data, image_bytes, image_name

        # application/json (or anything else we force-parse as JSON)
        data = request.get_json(force=True, silent=True) or {}
        b64 = data.get("image_base64") or data.get("image_b64")
        image_bytes = base64.b64decode(b64) if b64 else None
        image_name = data.get("image_file")
        return data, image_bytes, image_name

    @staticmethod
    def _form_to_dict(form):
        """Flat multipart form -> dict, decoding nested JSON fields if present."""
        data = dict(form)
        for key in ("bbox", "depth", "gps"):
            val = data.get(key)
            if isinstance(val, str):
                try:
                    data[key] = json.loads(val)
                except (ValueError, TypeError):
                    pass
        return data

    def _save_image(self, image_bytes, image_name):
        """Write image bytes under a path-safe version of the sender's name."""
        safe = secure_filename(image_name) if image_name else ""
        if not safe:
            safe = f"detection_{int(datetime.now().timestamp() * 1000)}.jpg"
        with open(os.path.join(self.image_dir, safe), "wb") as f:
            f.write(image_bytes)
        return safe

    # ------------------------------------------------------------------ #
    # Merge (correlate the initial + depth POSTs of one detection)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _track_id(data):
        """Return the detector's tracker id, under either spelling it uses.

        Current payloads name it ``tracker_id``; earlier ones (and
        mock_detector) send ``track_id``. Tested against None rather than
        truthiness so a legitimate id of 0 is not discarded.
        """
        for field in ("track_id", "tracker_id"):
            value = data.get(field)
            if value is not None:
                return value
        return None

    @staticmethod
    def _merge_key(data, image_name):
        """Stable key correlating a detection's POSTs: image_file, else timestamp."""
        if image_name:
            return secure_filename(image_name)
        if data.get("image_file"):
            return secure_filename(data["image_file"])
        if data.get("timestamp") is not None:
            return f"ts:{data['timestamp']}"
        if data.get("datetime"):
            return f"dt:{data['datetime']}"
        return None

    def _merge(self, incoming, key):
        """Upsert `incoming` into the record store, returning the live record."""
        with self._merge_lock:
            existing = self._index.get(key) if key is not None else None
            if existing is None:
                self._counter += 1
                incoming["id"] = self._counter
                if key is not None:
                    self._index[key] = incoming
                self.detections.append(incoming)
                return incoming
            self._merge_fields(existing, incoming)
            return existing

    @staticmethod
    def _merge_fields(dst, src):
        """Fold a later POST into the existing record, keeping id/track_id/image."""
        if src.get("class") and src["class"] != "unknown":
            dst["class"] = dst["object_class"] = src["class"]
        if src.get("track_id") is not None:
            dst["track_id"] = src["track_id"]
        if src.get("confidence"):
            dst["confidence"] = src["confidence"]
        if src.get("bbox") is not None:
            dst["bbox"] = src["bbox"]
        if src.get("depth") is not None:
            # A delayed/retried initial POST must never roll a completed depth
            # result back to "processing". This also makes the merge safe if
            # the two HTTP requests happen to be handled out of order.
            if should_replace_depth(dst.get("depth"), src.get("depth")):
                dst["depth"] = src["depth"]
                dst["distance_m"] = src.get("distance_m")
        if src.get("has_gps"):
            dst["latitude"] = src["latitude"]
            dst["longitude"] = src["longitude"]
            dst["has_gps"] = True
            dst["gps"] = src.get("gps")
        if src.get("altitude"):
            dst["altitude"] = src["altitude"]
        # Only the POST that carries equipment_info sets it, so a later POST
        # without it must not erase what the first one established.
        if src.get("equipment_info") is not None:
            dst["equipment_info"] = src["equipment_info"]
        if src.get("datetime"):
            dst["datetime"] = src["datetime"]
        if src.get("image_url") and not dst.get("image_url"):
            dst["image_url"] = src["image_url"]
        if src.get("image_file") and not dst.get("image_file"):
            dst["image_file"] = src["image_file"]

    def _normalize(self, data, image_name, image_url):
        """Map the incoming (new or old) schema to the dict the UI expects.

        The UI formats numbers directly (e.g. lat.toFixed, altitude:.1f), so
        every numeric field must be a real float here - never None. `id` is
        assigned later by _merge.
        """
        obj_class = data.get("class") or data.get("object_class") or "unknown"

        lat, lon, has_gps = self._parse_gps(data)
        depth = data.get("depth") if isinstance(data.get("depth"), dict) else None
        distance_m = self._to_float(depth.get("distance_m"), None) if depth else None

        return {
            "id": None,
            "track_id": self._track_id(data),
            "object_class": obj_class,
            "class": obj_class,
            "confidence": self._to_float(data.get("confidence"), 0.0),
            "latitude": lat,
            "longitude": lon,
            "has_gps": has_gps,
            "altitude": self._to_float(data.get("altitude"), 0.0),
            "heading": self._to_float(data.get("heading"), 0.0),
            "bbox": data.get("bbox"),
            "depth": depth,
            "distance_m": distance_m,
            "gps": data.get("gps"),
            # Carried through verbatim: the UI derives the checkpoint search
            # radius from equipment_info.max_range_km.
            "equipment_info": data.get("equipment_info"),
            "timestamp": self._normalize_timestamp(data),
            "datetime": data.get("datetime"),
            "image_file": image_name,
            "image_url": image_url,
        }

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _persist(self, detection, key):
        """Write the merged per-detection JSON file + append to the event log."""
        stem = secure_filename(os.path.splitext(key or "")[0]) if key else ""
        if not stem:
            stem = f"detection_{detection['id']}"
        try:
            with self._file_lock:
                with open(os.path.join(self.record_dir, stem + ".json"),
                          "w", encoding="utf-8") as f:
                    json.dump(detection, f, indent=2, ensure_ascii=False)
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(detection, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[DetectionServer] could not persist detection: {e}")

    # ------------------------------------------------------------------ #
    # Small parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_float(value, default=0.0):
        """Best-effort float; returns `default` for None/blank/garbage."""
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _parse_gps(cls, data):
        """Return (lat, lon, has_gps) from various gps shapes.

        Accepts gps as {"lat","lon"} / {"latitude","longitude"} / [lat, lon],
        or top-level latitude/longitude (old schema). Falls back to (0.0, 0.0).
        """
        gps = data.get("gps")
        lat = lon = None

        if isinstance(gps, dict):
            lat = gps.get("lat", gps.get("latitude"))
            lon = gps.get("lon", gps.get("lng", gps.get("longitude")))
        elif isinstance(gps, (list, tuple)) and len(gps) >= 2:
            lat, lon = gps[0], gps[1]

        # Top-level lat/lon (old schema or flat form) take over if present.
        if data.get("latitude") is not None:
            lat = data.get("latitude")
        if data.get("longitude") is not None:
            lon = data.get("longitude")

        has_gps = lat is not None and lon is not None
        return cls._to_float(lat, 0.0), cls._to_float(lon, 0.0), has_gps

    @staticmethod
    def _normalize_timestamp(data):
        """Produce an ISO-8601 string that both Python and JS Date can parse."""
        dt_str = data.get("datetime")
        if dt_str:
            try:
                # fromisoformat accepts "YYYY-MM-DD HH:MM:SS" (space separator)
                return datetime.fromisoformat(str(dt_str)).isoformat()
            except ValueError:
                pass

        ts = data.get("timestamp")
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).isoformat()
        if isinstance(ts, str) and ts:
            try:
                return datetime.fromisoformat(ts).isoformat()
            except ValueError:
                return ts  # already some other string form; pass through

        return datetime.now().isoformat()

    def start(self):
        """Start Flask server in a daemon thread."""
        def run():
            # use_reloader=False so it works inside a thread
            self.app.run(host=self.bind_host, port=self.port,
                         debug=False, use_reloader=False, threaded=True)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

        print(f"Detection API listening on 0.0.0.0:{self.port} "
              f"(bind: {self.bind_host})")
        print(f"  Records:  {self.record_dir}")
        print(f"  Log:      {self.log_file}")
        print(f"  Images:   {self.image_dir}")
        print(f"  Local:    http://{self.host}:{self.port}/detection")
        ts_ip = self._tailscale_ip()
        if ts_ip:
            # The Tailscale 100.x IP is static (unlike the DHCP-assigned LAN
            # IP, which can change on every reconnect), so advertise only that.
            print(f"  Tailscale: http://{ts_ip}:{self.port}/detection  <-- send here")
        else:
            # No Tailscale: fall back to the (changing) LAN addresses.
            for ip in self._local_ipv4s():
                print(f"  Network:  http://{ip}:{self.port}/detection")

    @staticmethod
    def _local_ipv4s():
        """Best-effort list of this machine's non-loopback IPv4 addresses."""
        ips = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None,
                                           socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except OSError:
            pass
        return ips

    @staticmethod
    def _tailscale_ip():
        """Return this node's Tailscale 100.x IPv4 if the CLI is available."""
        for exe in ("tailscale",
                    r"C:\Program Files\Tailscale\tailscale.exe"):
            try:
                out = subprocess.run([exe, "ip", "-4"],
                                     capture_output=True, text=True, timeout=3)
                ip = out.stdout.strip().splitlines()[0].strip() if out.stdout else ""
                if ip.startswith("100."):
                    return ip
            except (OSError, IndexError, subprocess.SubprocessError):
                continue
        return None

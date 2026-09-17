"""Offline tile cache/proxy.

Serves OpenStreetMap-style map tiles with a disk cache so the map keeps
working without internet:

  * Online  -> fetch tile from OSM, save to cache dir, return it.
  * Offline -> serve the cached tile if present.
  * No tile at all -> return a small transparent grid PNG so the map canvas,
    flight path, and detection markers still render.

Tiles are stored as   <cache_dir>/<z>/<x>/<y>.png   (standard slippy-map layout),
so an existing tile set (e.g. downloaded with a tile scraper) can be dropped in.
"""
import os
import io
import math
import time
import hashlib
import threading
import requests
from requests.adapters import HTTPAdapter

# A 256x256 fully-transparent PNG (1x1 scaled by the browser). Tiny, no PIL needed.
_BLANK_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc````\x00"
    b"\x00\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Public OSM tile server. Respect usage policy: cache aggressively, low volume.
_OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# OSM's usage policy blocks User-Agents that look like bulk/offline scrapers
# (a UA containing "offline tile cache" gets served a placeholder block tile
# with HTTP 200). Identify as a normal app; overridable via config.
_USER_AGENT = "drone-gcs/1.0 (+https://github.com/Diwansu-Pilania/drone-gcs)"

# OSM serves a fixed "access blocked" placeholder (HTTP 200) when it throttles
# a client. It must never be cached or served as a real tile. Identified by its
# exact SHA-256 so a legitimately 6987-byte tile is never mistaken for it.
_BLOCK_TILE_SHA256 = "b02c44252dac5a5e820ecef1e9bf9200e9407c042df668a466a1aa81a9ecca7a"

# Per-tile upstream fetch timeout (connect, read) seconds. Zooming in on an
# uncached area fetches many tiles at once; a cold fetch can take longer than
# the old 4s cap, so allow a generous read window while failing fast on connect.
_FETCH_TIMEOUT = (4, 12)

# How long to stay in "offline" mode after repeated network failures before
# probing the network again. A single dropped tile must NOT blind a layer for
# the whole session, so going offline is deferred until several consecutive
# failures and always expires so the map self-heals when the net returns.
_OFFLINE_RETRY_SECONDS = 20
_OFFLINE_FAIL_THRESHOLD = 4


def _deg2tile(lat, lon, z):
    """Convert lat/lon to slippy-map tile x/y at zoom z (standard OSM formula)."""
    lat_r = math.radians(lat)
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    # Clamp to valid range (poles / antimeridian edge cases).
    x = min(max(x, 0), n - 1)
    y = min(max(y, 0), n - 1)
    return x, y


class TileCache:
    """Disk-backed slippy-map tile cache with online fetch fallback."""

    def __init__(self, cache_dir, online=True, url_template=_OSM_URL,
                 user_agent=None):
        self.cache_dir = cache_dir
        self.online = online  # master switch: may this cache ever fetch online?
        self.url_template = url_template
        os.makedirs(self.cache_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent or _USER_AGENT})
        # Pool sized for the burst a zoom/pan triggers (a screenful of tiles at
        # once) so connections are kept alive and reused instead of doing a
        # fresh TLS handshake per tile.
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=32, max_retries=0)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        # Circuit breaker: consecutive network errors trip a temporary offline
        # window (so genuinely-offline use doesn't hang on every tile), which
        # then expires and re-probes. Guarded because many threads fetch at once.
        self._net_lock = threading.Lock()
        self._fail_streak = 0
        self._offline_until = 0.0  # monotonic time; > now -> skip live fetches

    def _tile_path(self, z, x, y):
        return os.path.join(self.cache_dir, str(z), str(x), f"{y}.png")

    def get_tile(self, z, x, y):
        """Return (bytes, is_blank). Never raises for a normal miss."""
        path = self._tile_path(z, x, y)

        # 1. Cache hit
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return f.read(), False
            except OSError:
                pass  # fall through to fetch/blank

        # 2. Try to fetch unless the breaker has us in an offline window
        if self._should_try_online():
            tile, neterr = self._fetch(z, x, y)
            self._note_fetch_result(neterr)
            if tile is not None:
                self._save(path, tile)
                return tile, False

        # 3. Nothing available -> transparent tile so the map still renders
        return _BLANK_PNG, True

    def _should_try_online(self):
        """Whether a live fetch should be attempted right now."""
        if not self.online:
            return False
        with self._net_lock:
            return time.monotonic() >= self._offline_until

    def _note_fetch_result(self, neterr):
        """Feed a fetch outcome to the breaker: trip after a run of failures,
        reset immediately on any success so the map self-heals."""
        with self._net_lock:
            if neterr:
                self._fail_streak += 1
                if self._fail_streak >= _OFFLINE_FAIL_THRESHOLD:
                    self._offline_until = time.monotonic() + _OFFLINE_RETRY_SECONDS
            else:
                self._fail_streak = 0
                self._offline_until = 0.0

    def _fetch(self, z, x, y):
        """Fetch one tile. Returns (bytes_or_None, network_error).

        network_error is True only for connection/timeout failures, so callers
        can distinguish "the network is down" from "the server returned 404".
        Has no side effects on self.online (each caller decides what to do).
        """
        url = self.url_template.format(z=z, x=x, y=y)
        try:
            resp = self._session.get(url, timeout=_FETCH_TIMEOUT)
            if resp.status_code == 200 and resp.content:
                # A throttled client gets the "access blocked" placeholder with
                # HTTP 200 — treat it as a miss so it isn't cached or served.
                if hashlib.sha256(resp.content).hexdigest() == _BLOCK_TILE_SHA256:
                    return None, False
                return resp.content, False
            return None, False  # got a response (e.g. 404) -> not a network error
        except requests.RequestException:
            return None, True

    def _save(self, path, data):
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
            except OSError:
                pass  # cache write failure is non-fatal

    def prefetch_area(self, lat, lon, zooms, radius=2):
        """Download and cache tiles around (lat, lon) for offline use.

        Fetches an (2*r+1) x (2*r+1) block of tiles at each zoom level, where r
        is `radius` (an int applied to every zoom) or, for finer control, a
        {zoom: radius} dict — higher zooms need a larger radius to cover the
        same ground area. Already-cached tiles are skipped. Safe to call in a
        background thread; silently stops fetching if the network drops.

        Returns the number of tiles newly fetched.
        """
        fetched = 0
        for z in zooms:
            r = radius.get(z, 2) if isinstance(radius, dict) else radius
            cx, cy = _deg2tile(lat, lon, z)
            n = 2 ** z
            for x in range(cx - r, cx + r + 1):
                for y in range(cy - r, cy + r + 1):
                    if not (0 <= x < n and 0 <= y < n):
                        continue
                    path = self._tile_path(z, x, y)
                    if os.path.exists(path):
                        continue
                    tile, neterr = self._fetch(z, x, y)
                    self._note_fetch_result(neterr)
                    if tile is not None:
                        self._save(path, tile)
                        fetched += 1
                    elif neterr:
                        return fetched  # network down; stop the prefetch sweep
        return fetched

    def cached_tile_count(self):
        count = 0
        for _root, _dirs, files in os.walk(self.cache_dir):
            count += sum(1 for f in files if f.endswith(".png"))
        return count


def guess_image_mime(data):
    """Sniff an image's MIME type from its magic bytes.

    Tiles are cached on disk as .png regardless of real format, and providers
    differ (OSM serves PNG, Esri World Imagery serves JPEG). The browser needs
    the true Content-Type, so detect it from the bytes rather than the filename.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] in (b"RIFF",) and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"  # safe default; Leaflet/browsers sniff anyway


class TileCacheGroup:
    """A named set of TileCaches (one per map layer), sharing config.

    Layers are defined by a spec dict (see config.MAP_TILE_LAYERS): each has its
    own provider URL template and an on-disk cache under <cache_root>/<name>/.
    The map proxies every layer through /tiles/<name>/{z}/{x}/{y}.png.
    """

    def __init__(self, cache_root, layers, online=True, user_agent=None):
        self.cache_root = cache_root
        self.layers = dict(layers)
        self.caches = {
            name: TileCache(
                os.path.join(cache_root, name),
                online=online,
                url_template=spec["url"],
                user_agent=user_agent,
            )
            for name, spec in self.layers.items()
        }
        # Base layer shown first if a request omits the layer name.
        self.default_base = next(
            (n for n, s in self.layers.items() if s.get("default") and not s.get("overlay")),
            next(iter(self.caches), None),
        )

    def get_tile(self, layer, z, x, y):
        """Return (bytes, is_blank) for a layer; blank tile for an unknown one."""
        cache = self.caches.get(layer)
        if cache is None:
            return _BLANK_PNG, True
        return cache.get_tile(z, x, y)

    def default_layer_names(self):
        """Names of layers shown on startup (used for offline prefetch)."""
        return [n for n, s in self.layers.items() if s.get("default")]

    def prefetch_area(self, lat, lon, zooms, radius=2, layers=None):
        """Prefetch the given layers (default: the startup-visible ones)."""
        names = layers if layers is not None else self.default_layer_names()
        total = 0
        for name in names:
            cache = self.caches.get(name)
            if cache is not None:
                total += cache.prefetch_area(lat, lon, zooms, radius)
        return total

    def cached_tile_count(self):
        return sum(c.cached_tile_count() for c in self.caches.values())


BLANK_TILE = _BLANK_PNG

"""Configuration for drone GCS"""

# MAVLink connection settings
MAVLINK_CONNECTION = "COM11"  # Change to your serial port or "udp:127.0.0.1:14550" for SITL
BAUD_RATE = 57600

# Auto-connect on startup using the settings above (skip the Connect dialog)
AUTO_CONNECT = True

# Detection API server settings
# BIND_HOST is what the server listens on. Use "0.0.0.0" to accept connections
# from other machines (Tailscale peers, LAN) — "127.0.0.1" would only accept
# requests from this computer. HOST is what gets baked into image/tile URLs that
# the LOCAL dashboard fetches, so it stays loopback.
DETECTION_API_HOST = "127.0.0.1"
DETECTION_API_BIND_HOST = "0.0.0.0"
DETECTION_API_PORT = 5000

# Map default settings
# Legacy single-layer URL (kept for reference/back-compat). The map now uses the
# multi-layer MAP_TILE_LAYERS below; this is the plain OSM street layer's URL.
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

# User-Agent sent when fetching tiles. OSM's tile usage policy REQUIRES a UA
# that identifies the app, and it blocks scraper-like UAs (anything containing
# "offline"/"cache") by serving an "access blocked" placeholder image instead
# of the map. Keep it identifying and, ideally, add a contact URL/email.
MAP_TILE_USER_AGENT = "drone-gcs/1.0 (+https://github.com/Diwansu-Pilania/drone-gcs)"

# --- Map tile layers -------------------------------------------------------
# Each layer is fetched + disk-cached by the GCS tile server (core/tile_server)
# and exposed to the map at /tiles/<name>/{z}/{x}/{y}.png. Leaflet always talks
# to our proxy in standard XYZ order; each provider's own tile ordering lives in
# its URL template below — note Esri uses {z}/{y}/{x} (y before x).
#
# The map opens on satellite imagery with road + place-name overlays for a
# "Google-Maps-like" view; a layer switcher (top-right) toggles the plain
# OpenStreetMap street map and the label overlays. All providers are keyless.
#
#   base=True     -> selectable base map (radio button; exactly one shown)
#   overlay=True  -> transparent layer drawn on top (checkbox)
#   default=True  -> shown on startup
MAP_TILE_LAYERS = {
    "satellite": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS community",
        "max_zoom": 19,
        "base": True,
        "default": True,
    },
    "street": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
        "max_zoom": 19,
        "base": True,
    },
    "roads": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/"
               "Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
        "attribution": "",
        "max_zoom": 19,
        "overlay": True,
        "default": True,
    },
    "places": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/"
               "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        "attribution": "",
        "max_zoom": 19,
        "overlay": True,
        "default": True,
    },
}

# Home / default view (Army Institute of Technology, Dighi Hills, Pune).
DEFAULT_MAP_CENTER = [18.607139911174386, 73.87508649590356]  # AIT Pune
DEFAULT_MAP_ZOOM = 17

# View shown when the drone/system is offline: AIT campus, zoomed in.
OFFLINE_MAP_CENTER = [18.607139911174386, 73.87508649590356]  # AIT Pune
OFFLINE_MAP_ZOOM = 17

# On startup, pre-download the tiles around the offline center so the AIT map
# is available with no internet AND the first zoom-in is instant (no "loading").
# Runs once in the background; already-cached tiles are skipped, so later
# startups are effectively free.
PREFETCH_OFFLINE_TILES = True
OFFLINE_PREFETCH_ZOOMS = [14, 15, 16, 17, 18, 19]
# Tiles fetched around the center per zoom = (2*r+1)^2. A fixed radius under-
# covers high zooms (a tile covers less ground), so give the close-in zooms a
# wider radius. Radius maps zoom -> r; missing zooms default to 2.
OFFLINE_PREFETCH_RADIUS = {14: 2, 15: 2, 16: 3, 17: 3, 18: 4, 19: 4}

# Image storage
DETECTION_IMAGE_DIR = "detection_images"

# Offline map tiles
# Tiles are cached to disk here as <z>/<x>/<y>.png. When online, missing tiles
# are fetched from OpenStreetMap and saved. When offline, cached tiles are served
# and any gaps show a blank grid (path/markers still render).
TILE_CACHE_DIR = "map_tiles"


"""Constants for the NX Witness integration."""

DOMAIN = "nx_witness"
DEFAULT_PORT = 7001
UPDATE_INTERVAL = 30  # seconds - how often to refresh camera list

OBJECT_TRACK_INTERVAL = 5  # seconds - how often to check for object tracks
OBJECT_TRACK_TIMEOUT = 30  # seconds - how long to keep sensor "on" after detection

# Object types from NX Witness
OBJECT_TYPES = {
    "nx.base.Person": "person",
    "nx.base.Vehicle": "vehicle",
    "nx.base.Face": "face",
}
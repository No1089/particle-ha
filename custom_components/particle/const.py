"""Constants for the Particle integration."""

DOMAIN = "particle"
API = "https://api.particle.io/v1"

CONF_DEVICES = "devices"
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 300  # s; each variable read is a cloud round trip (data on cellular)

HA_PREFIX = "ha/"  # events named ha/<key> become sensors
BUS_EVENT = "particle_event"
SIGNAL_HA_VALUE = "particle_ha_value_{entry_id}"
SIGNAL_LIGHT = "particle_light_{entry_id}"
SYNC_EVENT = "hass-sync"  # published on every stream (re)connect; devices answer with their state

STORE_VERSION = 1

# Event stream supervision
PROBE_EVENT = "hass-ping"  # private canary we publish and expect back ("particle*" is reserved)
PROBE_INTERVAL = 300  # s between canaries
PROBE_TIMEOUT = 45  # s for the canary to come back before the stream counts as stuck
PROBE_SETTLE = 2  # s after the stream opens before the first canary
BACKOFF_MIN, BACKOFF_MAX = 5, 300
DOWN_ISSUE_AFTER = 900  # s without a healthy stream before a Repairs issue is raised
SIGNAL_STREAM = "particle_stream_{entry_id}"

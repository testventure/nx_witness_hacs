"""Binary sensor platform for NX Witness."""
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, EVENT_SENSOR_TIMEOUT
from .coordinator import NXWitnessDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case without regex."""
    result = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and (name[i - 1].islower() or name[i - 1].isdigit()):
            result.append("_")
        result.append(ch.lower())
    return "".join(result)


_EVENT_TYPE_MAP: dict[str, str] = {
    "nx.base.MotionEvent": "motion",
    "nx.base.InputSignalEvent": "input_signal",
    "nx.base.NetworkIssueEvent": "network_issue",
    "nx.base.CameraDisconnectedEvent": "camera_disconnected",
    "nx.base.CameraIpConflictEvent": "camera_ip_conflict",
    "nx.base.StorageFailureEvent": "storage_failure",
    "nx.base.ServerStartedEvent": "server_started",
    "nx.base.LicenseIssueEvent": "license_issue",
    "nx.analytics.ObjectDetected": "object_detected",
    "nx.analytics.BestShot": "best_shot",
    "nx.stub.SoftTrigger": "soft_trigger",
    "nx.base.UserDefinedEvent": "user_defined",
    "nx.base.GenericEvent": "generic",
}


def _clean_event_type(raw_type: str) -> str:
    """Return a human-readable event type from a raw NX Witness type string."""
    if not raw_type:
        return "unknown"
    if raw_type in _EVENT_TYPE_MAP:
        return _EVENT_TYPE_MAP[raw_type]
    for prefix in ("nx.analytics.", "nx.base.", "nx.stub.", "nx.", "cvedia.rt.", "cvedia."):
        if raw_type.startswith(prefix):
            raw_type = raw_type[len(prefix):]
            break
    if raw_type.lower().endswith("event"):
        raw_type = raw_type[:-5]
    return _camel_to_snake(raw_type) or "unknown"


def _extract_object_class(event: dict[str, Any]) -> str | None:
    """Extract the detected object class from an analytics event."""
    event_data = event.get("eventData")
    if isinstance(event_data, dict):
        object_type_id = event_data.get("objectTypeId")
        if isinstance(object_type_id, str) and object_type_id:
            leaf = object_type_id.rsplit(".", 1)[-1]
            if leaf:
                return leaf
        for field in ("objectType", "typeId", "objectClass"):
            value = event_data.get(field)
            if isinstance(value, str) and value:
                return value
        attributes = event_data.get("attributes")
        if isinstance(attributes, dict):
            for field in ("class", "objectClass", "type"):
                value = attributes.get(field)
                if isinstance(value, str) and value:
                    return value
        elif isinstance(attributes, list):
            attr_map = {
                item["name"]: item.get("value")
                for item in attributes
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            for field in ("class", "objectClass", "type"):
                value = attr_map.get(field)
                if isinstance(value, str) and value:
                    return value
    # Fall back to the middle segment of a "Type - Class - Zone" caption
    for source in (event, _event_payload(event)):
        caption = source.get("caption")
        if isinstance(caption, str) and caption.strip():
            classification, _ = _parse_caption_parts(caption)
            if classification:
                return classification
    return None


def _extract_event_description(event: dict[str, Any]) -> str | None:
    """Extract a human-readable description or message from an event."""
    event_data = event.get("eventData")
    sources = (event_data, event) if isinstance(event_data, dict) else (event,)
    for source in sources:
        for field in ("description", "message", "caption"):
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_event_state(event: dict[str, Any]) -> str:
    """Return a normalized event state: 'detected' or 'stopped'."""
    payload = _event_payload(event)
    raw = str(payload.get("state") or event.get("state") or "").lower().strip()
    if raw in {"stopped", "stop", "ended", "end", "inactive", "off"}:
        return "stopped"
    return "detected"


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Return the nested event payload when available."""
    nested = event.get("eventData")
    if isinstance(nested, dict):
        return nested
    return event


def _extract_camera_id(event: dict[str, Any]) -> str | None:
    """Extract camera id from an event payload."""
    payload = _event_payload(event)
    for source in (payload, event):
        for field in ("cameraId", "deviceId", "resourceId", "sourceId"):
            value = source.get(field)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_event_type_raw(event: dict[str, Any]) -> str:
    """Extract the raw event type identifier, prioritising type fields over captions."""
    # Check top-level event first for explicit type identifiers
    for field in ("eventType", "eventTypeId", "type"):
        value = event.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Fall back to eventData type fields
    payload = _event_payload(event)
    if payload is not event:
        for field in ("eventType", "eventTypeId", "type"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    # Last resort: name/caption
    for source in (event, payload):
        for field in ("name", "caption"):
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "Unknown"


def _parse_caption_parts(caption: str) -> tuple[str | None, str | None]:
    """Split a caption like 'Type - Class - Zone Name' into (classification, zone).

    Returns (None, caption) when the pattern doesn't match.
    """
    parts = [p.strip() for p in caption.split(" - ")]
    if len(parts) >= 3:
        # parts[0] = detection type (redundant with event_type), parts[1] = class, rest = zone
        classification = parts[1] if parts[1] else None
        zone = " - ".join(parts[2:])
        return classification, zone
    return None, caption.strip()


def _extract_area(event: dict[str, Any]) -> str | None:
    """Extract the zone/rule name from an event caption, stripping the leading type and class."""
    for source in (event, _event_payload(event)):
        value = source.get("caption")
        if isinstance(value, str) and value.strip():
            _, zone = _parse_caption_parts(value)
            return zone
    return None


def _extract_event_name(event: dict[str, Any]) -> str:
    """Extract user-facing event name."""
    payload = _event_payload(event)
    for source in (payload, event):
        for field in ("caption", "name", "eventType", "eventTypeId", "type"):
            value = source.get(field)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return "Unknown"


def _extract_analytics_attributes(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract analytics attributes from eventData.attributes list.

    NX Witness returns attributes as a list of {name, value} dicts.
    Keys are normalised to snake_case so they are safe to use in templates.
    Returns None when no attributes are present.
    """
    payload = _event_payload(event)
    attributes = payload.get("attributes")
    if not isinstance(attributes, list):
        return None
    result: dict[str, Any] = {}
    for item in attributes:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and name.strip():
            key = _camel_to_snake(name.strip()).replace(" ", "_")
            result[key] = value
    return result or None


def _extract_event_timestamp_ms(event: dict[str, Any]) -> int:
    """Extract a best-effort detection timestamp from event payload."""
    payload = _event_payload(event)

    for source in (event, payload):
        for field in ("timestampMs", "createdTimeMs", "startTimeMs", "endTimeMs"):
            value = source.get(field)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.isdigit():
                return int(value)

        # NX may provide microseconds in `timestamp`.
        value = source.get("timestamp")
        if isinstance(value, str) and value.isdigit():
            ts_value = int(value)
            if ts_value > 10_000_000_000_000:
                return int(ts_value / 1000)
            return ts_value

    time_period = event.get("timePeriod")
    if isinstance(time_period, dict):
        for field in ("endTimeMs", "startTimeMs"):
            value = time_period.get(field)
            if isinstance(value, (int, float)):
                return int(value)

    return 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NX Witness binary sensors."""
    coordinator: NXWitnessDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_camera_ids: set[str] = set()

    def _create_camera_event_sensors() -> list[NXWitnessEventSensor]:
        new_sensors: list[NXWitnessEventSensor] = []

        for camera in coordinator.data.get("cameras", []):
            camera_id = camera.get("id")
            if not camera_id or camera_id in known_camera_ids:
                continue

            known_camera_ids.add(camera_id)
            camera_name = camera.get("name", f"Camera {camera_id}")
            new_sensors.append(
                NXWitnessEventSensor(
                    coordinator,
                    camera_id,
                    camera_name,
                )
            )

        return new_sensors

    initial_sensors = _create_camera_event_sensors()
    if initial_sensors:
        async_add_entities(initial_sensors)

    def _handle_coordinator_update() -> None:
        new_sensors = _create_camera_event_sensors()
        if new_sensors:
            _LOGGER.debug("Adding %d new camera event sensors", len(new_sensors))
            async_add_entities(new_sensors)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class NXWitnessEventSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of an NX Witness camera event sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NXWitnessDataUpdateCoordinator,
        camera_id: str,
        camera_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._camera_id = camera_id

        self._attr_name = "Event"
        self._attr_unique_id = f"{DOMAIN}_{camera_id}_event"
        self._attr_device_class = BinarySensorDeviceClass.MOTION

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, camera_id)},
            name=camera_name,
            manufacturer="Network Optix",
            via_device=(DOMAIN, coordinator.host),
        )

        self._last_detection_time: datetime | None = None
        self._last_event_type_clean: str | None = None
        self._last_classification: str | None = None
        self._last_area: str | None = None
        self._last_event_description: str | None = None
        self._last_event_state: str | None = None
        self._last_analytics_attributes: dict[str, Any] | None = None
        self._active_events: list[dict[str, Any]] = []

    def _event_matches_sensor(self, event: dict[str, Any]) -> bool:
        """Return True when an event belongs to this camera sensor."""
        event_camera_id = _extract_camera_id(event)
        if event_camera_id != self._camera_id:
            return False

        # Most CV events report started/instant; ignore explicit stop states.
        event_state = str(_event_payload(event).get("state") or "").lower()
        if event_state in {"stopped", "stop", "ended", "end"}:
            return False

        return True

    @property
    def is_on(self) -> bool:
        """Return true if any matching event was detected recently."""
        if not self.coordinator.last_update_success:
            return False

        events = self.coordinator.data.get("events", [])

        now = datetime.now()
        cutoff_time_ms = int((now - timedelta(seconds=EVENT_SENSOR_TIMEOUT)).timestamp() * 1000)

        # Collect ALL matching recent events so simultaneous alerts (e.g. an
        # analyticsObject person-detection event AND a separate analytics
        # intrusion-rule event arriving at the same time) are not silently
        # dropped by an early return.
        matching: list[tuple[int, dict[str, Any]]] = []
        for event in events:
            if not self._event_matches_sensor(event):
                continue
            event_timestamp = _extract_event_timestamp_ms(event)
            if event_timestamp >= cutoff_time_ms:
                matching.append((event_timestamp, event))

        if not matching:
            self._active_events = []
            return False

        # Most-recent event drives the primary sensor attributes.
        matching.sort(key=lambda x: x[0], reverse=True)
        best_ts, best_event = matching[0]

        self._last_detection_time = datetime.fromtimestamp(best_ts / 1000)
        self._last_event_type_clean = _clean_event_type(_extract_event_type_raw(best_event))
        self._last_classification = _extract_object_class(best_event)
        self._last_area = _extract_area(best_event)
        self._last_event_description = _extract_event_description(best_event)
        self._last_event_state = _extract_event_state(best_event)
        self._last_analytics_attributes = _extract_analytics_attributes(best_event)

        # Build a compact list of all concurrent events for templates/automations.
        active: list[dict[str, Any]] = []
        for ts_ms, event in matching:
            entry: dict[str, Any] = {
                "event_type": _clean_event_type(_extract_event_type_raw(event)),
                "timestamp": datetime.fromtimestamp(ts_ms / 1000).isoformat(),
            }
            classification = _extract_object_class(event)
            if classification:
                entry["classification"] = classification
            area = _extract_area(event)
            if area:
                entry["area"] = area
            description = _extract_event_description(event)
            if description:
                entry["description"] = description
            analytics_attrs = _extract_analytics_attributes(event)
            if analytics_attrs:
                entry["analytics_attributes"] = analytics_attrs
            active.append(entry)
        self._active_events = active

        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs: dict[str, Any] = {"camera_id": self._camera_id}

        if self._last_detection_time:
            attrs["last_detection"] = self._last_detection_time.isoformat()

        if self._last_event_type_clean:
            attrs["event_type"] = self._last_event_type_clean

        if self._last_classification:
            attrs["classification"] = self._last_classification

        if self._last_area:
            attrs["area"] = self._last_area

        if self._last_event_description:
            attrs["event_description"] = self._last_event_description

        if self._last_event_state:
            attrs["event_state"] = self._last_event_state

        if self._last_analytics_attributes:
            attrs["analytics_attributes"] = self._last_analytics_attributes

        if len(self._active_events) > 1:
            attrs["active_events"] = self._active_events

        return attrs

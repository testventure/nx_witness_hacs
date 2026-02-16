"""Binary sensor platform for NX Witness."""
import logging
import re
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

from .const import DOMAIN, OBJECT_TRACK_TIMEOUT
from .coordinator import NXWitnessDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


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


def _extract_event_key(event: dict[str, Any]) -> str:
    """Build a stable, sensor-level key from event metadata."""
    payload = _event_payload(event)

    event_type = str(payload.get("eventTypeId") or payload.get("type") or "unknown").strip()
    # Caption separates custom zones/classes while eventTypeId can be shared.
    caption = str(payload.get("caption") or payload.get("name") or "").strip()

    if caption:
        return f"{event_type}:{caption}"
    return event_type


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


def _slugify(value: str) -> str:
    """Create an entity-safe suffix."""
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NX Witness binary sensors."""
    coordinator: NXWitnessDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    cameras_by_id = {
        camera["id"]: camera.get("name", f"Camera {camera['id']}")
        for camera in coordinator.data.get("cameras", [])
        if camera.get("id")
    }

    known_sensor_keys: set[tuple[str, str]] = set()

    def _create_new_event_sensors() -> list[NXWitnessEventSensor]:
        new_sensors: list[NXWitnessEventSensor] = []

        for event in coordinator.data.get("events", []):
            camera_id = _extract_camera_id(event)
            if not camera_id:
                continue

            event_key = _extract_event_key(event)
            sensor_key = (camera_id, event_key)
            if sensor_key in known_sensor_keys:
                continue

            known_sensor_keys.add(sensor_key)
            camera_name = cameras_by_id.get(camera_id, f"Camera {camera_id}")
            event_name = _extract_event_name(event)
            new_sensors.append(
                NXWitnessEventSensor(
                    coordinator,
                    camera_id,
                    camera_name,
                    event_key,
                    event_name,
                )
            )

        return new_sensors

    initial_sensors = _create_new_event_sensors()
    if initial_sensors:
        async_add_entities(initial_sensors)

    def _handle_coordinator_update() -> None:
        for camera in coordinator.data.get("cameras", []):
            camera_id = camera.get("id")
            if camera_id:
                cameras_by_id[camera_id] = camera.get("name", f"Camera {camera_id}")

        new_sensors = _create_new_event_sensors()
        if new_sensors:
            _LOGGER.debug("Adding %d new event sensors", len(new_sensors))
            async_add_entities(new_sensors)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class NXWitnessEventSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of an NX Witness event sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NXWitnessDataUpdateCoordinator,
        camera_id: str,
        camera_name: str,
        event_key: str,
        event_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._camera_id = camera_id
        self._event_key = event_key
        self._event_name = event_name

        self._attr_name = f"{event_name} Event"
        unique_suffix = _slugify(event_key)
        self._attr_unique_id = f"{DOMAIN}_{camera_id}_{unique_suffix}"
        self._attr_device_class = BinarySensorDeviceClass.MOTION

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, camera_id)},
            name=camera_name,
            manufacturer="Network Optix",
            via_device=(DOMAIN, coordinator.host),
        )

        self._last_detection_time: datetime | None = None

    def _event_matches_sensor(self, event: dict[str, Any]) -> bool:
        """Return True when an event belongs to this camera/event sensor."""
        event_camera_id = _extract_camera_id(event)
        if event_camera_id != self._camera_id:
            return False

        if _extract_event_key(event) != self._event_key:
            return False

        # Most CV events report started/instant; ignore explicit stop states.
        event_state = str(_event_payload(event).get("state") or "").lower()
        if event_state in {"stopped", "stop", "ended", "end"}:
            return False

        return True

    @property
    def is_on(self) -> bool:
        """Return true if matching event detected recently."""
        if not self.coordinator.last_update_success:
            return False

        events = self.coordinator.data.get("events", [])

        now = datetime.now()
        cutoff_time_ms = int((now - timedelta(seconds=OBJECT_TRACK_TIMEOUT)).timestamp() * 1000)

        for event in events:
            if not self._event_matches_sensor(event):
                continue

            event_timestamp = _extract_event_timestamp_ms(event)
            if event_timestamp >= cutoff_time_ms:
                self._last_detection_time = datetime.fromtimestamp(event_timestamp / 1000)
                return True

        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "camera_id": self._camera_id,
            "event_type": self._event_name,
            "event_key": self._event_key,
        }

        if self._last_detection_time:
            attrs["last_detection"] = self._last_detection_time.isoformat()

        return attrs

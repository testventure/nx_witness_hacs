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
        self._last_event_name: str | None = None

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

        for event in events:
            if not self._event_matches_sensor(event):
                continue

            event_timestamp = _extract_event_timestamp_ms(event)
            if event_timestamp >= cutoff_time_ms:
                self._last_detection_time = datetime.fromtimestamp(event_timestamp / 1000)
                self._last_event_name = _extract_event_name(event)
                return True

        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "camera_id": self._camera_id,
        }

        if self._last_event_name:
            attrs["last_event_type"] = self._last_event_name

        if self._last_detection_time:
            attrs["last_detection"] = self._last_detection_time.isoformat()

        return attrs

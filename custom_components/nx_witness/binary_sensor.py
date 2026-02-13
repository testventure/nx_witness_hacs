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

from .const import DOMAIN, OBJECT_TRACK_TIMEOUT, OBJECT_TYPES
from .coordinator import NXWitnessDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NX Witness binary sensors."""
    coordinator: NXWitnessDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = []
    for camera_data in coordinator.data.get("cameras", []):
        camera_id = camera_data["id"]
        camera_name = camera_data.get("name", f"Camera {camera_id}")
        
        # Create a sensor for each object type
        for object_type_id, object_name in OBJECT_TYPES.items():
            sensors.append(
                NXWitnessObjectSensor(
                    coordinator,
                    camera_id,
                    camera_name,
                    object_type_id,
                    object_name,
                )
            )

    async_add_entities(sensors)


class NXWitnessObjectSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of an NX Witness object detection sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NXWitnessDataUpdateCoordinator,
        camera_id: str,
        camera_name: str,
        object_type_id: str,
        object_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._camera_id = camera_id
        self._object_type_id = object_type_id
        self._object_name = object_name
        
        self._attr_name = f"{object_name.title()} Detection"
        self._attr_unique_id = f"{DOMAIN}_{camera_id}_{object_name}"
        
        # Set device class based on object type
        if object_name == "person":
            self._attr_device_class = BinarySensorDeviceClass.MOTION
        elif object_name == "vehicle":
            self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
        else:
            self._attr_device_class = BinarySensorDeviceClass.MOTION

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, camera_id)},
            name=camera_name,
            manufacturer="Network Optix",
            via_device=(DOMAIN, coordinator.host),
        )

        self._last_detection_time = None

    @property
    def is_on(self) -> bool:
        """Return true if object detected recently."""
        if not self.coordinator.last_update_success:
            return False

        # Check object tracks from coordinator
        tracks = self.coordinator.data.get("object_tracks", [])
        
        now = datetime.now()
        cutoff_time_ms = int((now - timedelta(seconds=OBJECT_TRACK_TIMEOUT)).timestamp() * 1000)
        
        for track in tracks:
            # Check if track is for this camera and object type
            if (
                track.get("deviceId") == self._camera_id
                and track.get("objectTypeId") == self._object_type_id
            ):
                last_appearance = track.get("lastAppearanceTimeMs", 0)
                if last_appearance >= cutoff_time_ms:
                    self._last_detection_time = datetime.fromtimestamp(last_appearance / 1000)
                    return True
        
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "camera_id": self._camera_id,
            "object_type": self._object_name,
        }
        
        if self._last_detection_time:
            attrs["last_detection"] = self._last_detection_time.isoformat()
        
        return attrs
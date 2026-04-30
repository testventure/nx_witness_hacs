"""Camera platform for NX Witness."""
import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NXWitnessDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NX Witness cameras."""
    coordinator: NXWitnessDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    cameras = []
    for camera_data in coordinator.data.get("cameras", []):
        cameras.append(NXWitnessCamera(coordinator, camera_data))

    async_add_entities(cameras)


class NXWitnessCamera(CoordinatorEntity, Camera):
    """Representation of an NX Witness camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: NXWitnessDataUpdateCoordinator,
        camera_data: dict[str, Any],
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)

        self._camera_id = camera_data["id"]
        self._attr_name = camera_data.get("name", f"Camera {self._camera_id}")
        self._attr_unique_id = f"{DOMAIN}_{self._camera_id}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._camera_id)},
            name=self._attr_name,
            manufacturer="Network Optix",
            model=camera_data.get("model", "NX Camera"),
            via_device=(DOMAIN, coordinator.host),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False

        # Check if camera still exists in coordinator data
        for camera in self.coordinator.data.get("cameras", []):
            if camera["id"] == self._camera_id:
                # Consider camera available if status is Recording or Unauthorized
                status = camera.get("status", "").lower()
                return status in ["recording", "unauthorized", "online"]
        return False

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        try:
            # Get a ticket for this request
            ticket = await self.coordinator.client.get_ticket()
            if not ticket:
                _LOGGER.error("Failed to get ticket for camera image")
                return None
            
            # Get snapshot URL with ticket
            url = f"{self.coordinator.host}/rest/v4/devices/{self._camera_id}/image?_ticket={ticket}"
            
            async with self.coordinator.client.session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                _LOGGER.error("Failed to get camera image, status: %s", response.status)
        except Exception as ex:
            _LOGGER.error("Error getting camera image: %s", ex)
        return None

    async def stream_source(self) -> str:
        """Return the stream source."""
        # Force a fresh session token, then a fresh ticket, every time the
        # live view is requested. Stale tokens/tickets cause the live feed
        # to silently fail to load.
        ticket = await self.coordinator.client.get_ticket(force_new_token=True)
        if not ticket:
            _LOGGER.error("Failed to get ticket for stream")
            return None

        return self.coordinator.client.get_camera_stream_url(self._camera_id, ticket)

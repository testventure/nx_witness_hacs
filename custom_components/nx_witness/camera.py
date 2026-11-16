"""Camera platform for NX Witness."""
import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NXWitnessDataUpdateCoordinator
from .stream_view import stream_path_for

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NX Witness cameras."""
    coordinator: NXWitnessDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_camera_ids: set[str] = set()

    def _new_cameras() -> list["NXWitnessCamera"]:
        cameras = []
        for camera_data in coordinator.data.get("cameras", []):
            camera_id = camera_data.get("id")
            if not camera_id or camera_id in known_camera_ids:
                continue
            known_camera_ids.add(camera_id)
            cameras.append(NXWitnessCamera(coordinator, camera_data, entry.entry_id))
        return cameras

    async_add_entities(_new_cameras())

    def _handle_coordinator_update() -> None:
        # e.g. cameras that arrive with a newly merged server
        if cameras := _new_cameras():
            _LOGGER.debug("Adding %d new cameras", len(cameras))
            async_add_entities(cameras)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class NXWitnessCamera(CoordinatorEntity, Camera):
    """Representation of an NX Witness camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: NXWitnessDataUpdateCoordinator,
        camera_data: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)

        self._entry_id = entry_id
        self._camera_id = camera_data["id"]
        self._attr_name = camera_data.get("name", f"Camera {self._camera_id}")
        self._attr_unique_id = f"{DOMAIN}_{self._camera_id}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._camera_id)},
            name=self._attr_name,
            manufacturer="Network Optix",
            model=camera_data.get("model", "NX Camera"),
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
            # Ticket from the server that owns the camera (falls back to the
            # connected server, which proxies within a merged system).
            minted = await self.coordinator.async_camera_ticket(self._camera_id)
            if minted is None:
                _LOGGER.error("Failed to get ticket for camera image")
                return None
            client, ticket = minted
            url = client.get_camera_image_url(self._camera_id, ticket)

            async with client.session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                _LOGGER.error("Failed to get camera image, status: %s", response.status)
        except Exception as ex:
            _LOGGER.error("Error getting camera image: %s", ex)
        return None

    async def stream_source(self) -> str | None:
        """Return the stream source.

        NX tickets are single-use, but ffmpeg/go2rtc open the source URL
        more than once (probe + read + reconnects). We return a URL served
        by NXWitnessStreamView, which mints a fresh ticket per request and
        relays the media from whichever server owns the camera.
        """
        base = get_url(self.hass, allow_internal=True, prefer_external=False)
        path = stream_path_for(
            self._entry_id, self.coordinator.stream_secret, self._camera_id
        )
        return f"{base}{path}"

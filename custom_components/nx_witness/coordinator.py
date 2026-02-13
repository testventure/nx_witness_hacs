"""DataUpdateCoordinator for NX Witness."""
import logging
import ssl
from datetime import datetime, timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL
from .nx_client import NXWitnessClient

_LOGGER = logging.getLogger(__name__)


class NXWitnessDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching NX Witness data."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.host = host
        self.username = username
        self.password = password
        self.client = None
        self.cameras = []
        self.object_tracks = []
        self.last_track_check = datetime.now()
        self._session = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def _async_setup(self):
        """Set up the coordinator with SSL context."""
        # Create SSL context in executor to avoid blocking
        def create_ssl_context():
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context
        
        ssl_context = await self.hass.async_add_executor_job(create_ssl_context)
        
        # Create a connector with SSL disabled
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        # Create session with custom connector
        self._session = aiohttp.ClientSession(connector=connector)
        
        self.client = NXWitnessClient(
            self.host,
            self.username,
            self.password,
            self._session,
        )
        
        # Initial login
        if not await self.client.login():
            raise UpdateFailed("Failed to login to NX Witness")

    async def _async_update_data(self):
        """Update data via library."""
        try:
            from .const import OBJECT_TRACK_INTERVAL
            
            # Get cameras
            cameras = await self.client.get_cameras()
            self.cameras = cameras
            
            # Check for object tracks more frequently
            now = datetime.now()
            if (now - self.last_track_check).total_seconds() >= OBJECT_TRACK_INTERVAL:
                # Get tracks from last minute
                start_time_ms = int((now - timedelta(minutes=1)).timestamp() * 1000)
                tracks = await self.client.get_object_tracks(start_time_ms)
                self.last_track_check = now
                self.object_tracks = tracks
            
            return {
                "cameras": cameras,
                "object_tracks": self.object_tracks,
            }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with NX Witness: {err}") from err
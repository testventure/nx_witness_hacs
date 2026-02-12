"""Config flow for NX Witness integration."""
import logging
import ssl
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import DEFAULT_PORT, DOMAIN
from .nx_client import NXWitnessClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class NXWitnessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NX Witness."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Ensure host includes protocol
            if not host.startswith(("http://", "https://")):
                host = f"https://{host}"

            # Ensure host includes port if not specified
            if ":" not in host.split("//")[1]:
                host = f"{host}:{DEFAULT_PORT}"

            # Create SSL context in executor to avoid blocking
            def create_ssl_context():
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                return ssl_context
            
            ssl_context = await self.hass.async_add_executor_job(create_ssl_context)
            
            # Create connector with SSL disabled
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            session = aiohttp.ClientSession(connector=connector)
            
            client = NXWitnessClient(host, username, password, session)

            try:
                # Test connection
                if await client.login():
                    await session.close()
                    
                    # Create unique ID from host
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"NX Witness ({host})",
                        data={
                            CONF_HOST: host,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        },
                    )
                else:
                    errors["base"] = "cannot_connect"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                # Clean up session
                if not session.closed:
                    await session.close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

"""Config flow for Zehnder ConnectBox."""

from __future__ import annotations

import asyncio
from functools import partial

import voluptuous as vol
from homeassistant.components import network
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .client import ConnectBoxClient, PairingError
from .const import (
    CONF_APP_ID,
    CONF_APP_UUID,
    CONF_CERTIFICATE_SHA256,
    CONF_GATEWAY_UUID,
    CONF_REMOTE_UUID,
    DEFAULT_NAME,
    DOMAIN,
    PAIRING_TIMEOUT,
)
from .discovery import discover_gateways
from .models import DiscoveredGateway, PairingData

CONF_DISCOVERED_GATEWAY = "discovered_gateway"


class ZehnderConnectBoxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Guide local discovery and deliberate physical pairing."""

    VERSION = 1

    def __init__(self) -> None:
        self._gateways: dict[str, DiscoveredGateway] = {}
        self._selected: DiscoveredGateway | None = None
        self._pair_task: asyncio.Task[PairingData] | None = None

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Search the local network when the integration is added."""
        broadcast_addresses = await network.async_get_ipv4_broadcast_addresses(
            self.hass
        )
        targets = [str(address) for address in broadcast_addresses]
        if not targets:
            targets = ["255.255.255.255"]
        discovered = await self.hass.async_add_executor_job(discover_gateways, targets)
        self._update_known_hosts(discovered)

        configured = {
            entry.unique_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id is not None
        }
        self._gateways = {
            str(gateway_uuid): gateway
            for gateway_uuid, gateway in discovered.items()
            if str(gateway_uuid) not in configured
        }
        if not self._gateways:
            return self.async_show_menu(
                step_id="user", menu_options=("retry", "manual")
            )
        if len(self._gateways) == 1:
            self._selected = next(iter(self._gateways.values()))
            return await self.async_step_confirm()
        return await self.async_step_select()

    async def async_step_retry(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Repeat the bounded local discovery scan."""
        return await self.async_step_user()

    async def async_step_select(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Select one gateway when several were discovered."""
        if user_input is not None:
            self._selected = self._gateways[user_input[CONF_DISCOVERED_GATEWAY]]
            return await self.async_step_confirm()

        choices = {
            gateway_uuid: _gateway_label(gateway)
            for gateway_uuid, gateway in self._gateways.items()
        }
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {vol.Required(CONF_DISCOVERED_GATEWAY): vol.In(choices)}
            ),
        )

    async def async_step_manual(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Validate a manually supplied host using a local identity query."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            discovered = await self.hass.async_add_executor_job(
                discover_gateways, [host]
            )
            if not discovered:
                errors["base"] = "cannot_connect"
            else:
                self._selected = next(iter(discovered.values()))
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Confirm the selected gateway before starting physical pairing."""
        if self._selected is None:
            return self.async_abort(reason="discovery_failed")
        await self.async_set_unique_id(str(self._selected.gateway_uuid))
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._selected.host})
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self._selected.name or DEFAULT_NAME,
                "host": self._selected.host,
            },
        )

    async def async_step_pair(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Run physical pairing as a Home Assistant progress step."""
        if self._selected is None:
            return self.async_abort(reason="discovery_failed")
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(
                self._async_pair(), "Zehnder ConnectBox pairing"
            )
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pairing",
                progress_task=self._pair_task,
            )
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Create the config entry only after pairing completed."""
        if self._selected is None or self._pair_task is None:
            return self.async_abort(reason="pairing_failed")
        try:
            pairing = self._pair_task.result()
        except (PairingError, TimeoutError):
            self._pair_task = None
            return self.async_show_form(
                step_id="pair_failed",
                data_schema=vol.Schema({}),
                errors={"base": "pairing_failed"},
            )
        return self.async_create_entry(
            title=self._selected.name or DEFAULT_NAME,
            data={
                CONF_HOST: self._selected.host,
                CONF_GATEWAY_UUID: str(self._selected.gateway_uuid),
                CONF_APP_UUID: str(pairing.app_uuid),
                CONF_REMOTE_UUID: str(pairing.remote_uuid),
                CONF_APP_ID: pairing.app_id,
                CONF_CERTIFICATE_SHA256: pairing.certificate_sha256,
            },
        )

    async def async_step_pair_failed(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Let the user retry physical pairing without losing discovery."""
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(step_id="pair_failed", data_schema=vol.Schema({}))

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Update a gateway address without replacing its paired identity."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            discovered = await self.hass.async_add_executor_job(
                discover_gateways, [host]
            )
            expected_uuid = entry.data[CONF_GATEWAY_UUID]
            if not discovered:
                errors["base"] = "cannot_connect"
            elif expected_uuid not in {str(item) for item in discovered}:
                errors["base"] = "wrong_gateway"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}
            ),
            errors=errors,
        )

    async def _async_pair(self) -> PairingData:
        assert self._selected is not None
        return await self.hass.async_add_executor_job(
            partial(
                ConnectBoxClient.pair,
                self._selected.host,
                self._selected.gateway_uuid,
                timeout=PAIRING_TIMEOUT,
            )
        )

    def _update_known_hosts(self, discovered) -> None:
        """Refresh the stored address when a configured gateway moved."""
        entries = {
            entry.unique_id: entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        for gateway_uuid, gateway in discovered.items():
            entry = entries.get(str(gateway_uuid))
            if entry is not None and entry.data.get(CONF_HOST) != gateway.host:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: gateway.host}
                )


def _gateway_label(gateway: DiscoveredGateway) -> str:
    name = gateway.name or DEFAULT_NAME
    return f"{name} ({gateway.host})"

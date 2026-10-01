"""Gateway-wide controls for Zehnder ConnectBox."""

from __future__ import annotations

from datetime import UTC, datetime
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxGatewayEntity
from .models import GatewaySnapshot, RunMode
from .profiles import supports_summer_ventilation


def _supports_summer_ventilation(snapshot: GatewaySnapshot) -> bool:
    """Require a gateway setting and an explicitly capable supported unit."""
    return (
        snapshot.summer_ventilation_settings is not None
        and supports_summer_ventilation(snapshot.rooms)
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up standby and any reported summer ventilation control."""
    coordinator = entry.runtime_data
    async_add_entities([ConnectBoxVentilationSwitch(coordinator)])
    summer_added = False

    @callback
    def add_summer_when_supported() -> None:
        nonlocal summer_added
        if (
            summer_added
            or coordinator.data is None
            or not _supports_summer_ventilation(coordinator.data)
        ):
            return
        async_add_entities(
            [
                ConnectBoxSummerVentilationSwitch(coordinator),
                ConnectBoxSummerVentilationEnabledSwitch(coordinator),
            ]
        )
        summer_added = True

    add_summer_when_supported()
    entry.async_on_unload(coordinator.async_add_listener(add_summer_when_supported))


class ConnectBoxVentilationSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """Expose global standby as an on/off control."""

    _attr_translation_key = "central_ventilation"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_ventilation_power"

    @property
    def is_on(self) -> bool | None:
        """Return whether the gateway is outside standby."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.run_state.run_mode != RunMode.OFF

    async def async_turn_on(self, **kwargs) -> None:
        """Restore the most recently verified active mode."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Put all attached ventilation units into standby."""
        await self.coordinator.async_set_power(False)


class ConnectBoxSummerVentilationSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """Start or stop the app-configured summer interval for the gateway."""

    _attr_translation_key = "summer_ventilation"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_summer_ventilation"

    @property
    def is_on(self) -> bool | None:
        """Use the gateway's active flag and expiry timestamp."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.run_state.summer_ventilation_running(time.time())

    @property
    def available(self) -> bool:
        """Expose control only while the app setting and run mode allow it."""
        snapshot = self.coordinator.data
        settings = snapshot.summer_ventilation_settings if snapshot else None
        return bool(
            super().available
            and snapshot is not None
            and _supports_summer_ventilation(snapshot)
            and settings is not None
            and settings.enabled
            and settings.duration_hours is not None
            and 1 <= settings.duration_hours <= 24
            and snapshot.run_state.run_mode in (RunMode.AUTOMATIC, RunMode.MANUAL)
            and snapshot.run_state.temperature_mode in (0, 1, 2)
            and self.is_on is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Show the configured duration and reported end of an interval."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        settings = snapshot.summer_ventilation_settings
        end = snapshot.run_state.summer_ventilation_end
        try:
            ends_at = datetime.fromtimestamp(end, UTC).isoformat() if end else None
        except (OverflowError, OSError, ValueError):
            ends_at = None
        return {
            "duration_hours": settings.duration_hours if settings else None,
            "ends_at": ends_at,
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Start summer ventilation for the configured duration."""
        await self.coordinator.async_set_summer_ventilation(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the active summer ventilation interval."""
        await self.coordinator.async_set_summer_ventilation(False)


class ConnectBoxSummerVentilationEnabledSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """Configure whether the gateway permits summer ventilation."""

    _attr_translation_key = "summer_ventilation_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_summer_ventilation_enabled"

    @property
    def is_on(self) -> bool | None:
        """Return the gateway's function setting."""
        snapshot = self.coordinator.data
        settings = snapshot.summer_ventilation_settings if snapshot else None
        return settings.enabled if settings is not None else None

    @property
    def available(self) -> bool:
        """Avoid changing configuration during an active interval."""
        snapshot = self.coordinator.data
        return bool(
            super().available
            and snapshot is not None
            and _supports_summer_ventilation(snapshot)
            and snapshot.run_state.summer_ventilation is False
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the configured summer function."""
        await self.coordinator.async_set_summer_configuration(enabled=True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the summer function while preserving its duration."""
        await self.coordinator.async_set_summer_configuration(enabled=False)

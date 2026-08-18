"""Shared entity plumbing for the Helm integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN


def helm_device_info(entry_id: str, base_url: str) -> DeviceInfo:
    """Return the single service device every Helm entity hangs off."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="Helm",
        name=DEFAULT_NAME,
        configuration_url=base_url.removesuffix("/api/v1") or base_url,
    )


class HelmEntity(CoordinatorEntity):
    """Base entity wired to a Helm coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        from .const import CONF_BASE_URL  # noqa: PLC0415

        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = helm_device_info(
            entry.entry_id, entry.data[CONF_BASE_URL]
        )

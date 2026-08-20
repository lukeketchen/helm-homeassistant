"""Sensors summarising today's Helm data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .calendar import occurrence_field
from .const import CONF_BASE_URL, PLANNING_ENDPOINTS, PLANNING_TYPES
from .coordinator import HelmPlanningCoordinator, HelmShoppingCoordinator
from .entity import HelmEntity, helm_device_info

if TYPE_CHECKING:
    from . import HelmConfigEntry

MAX_STATE_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class HelmPlanningSensorDescription(SensorEntityDescription):
    """Describes a sensor derived from the planning coordinator."""

    value_fn: Callable[[HelmPlanningCoordinator, date], Any]
    attrs_fn: Callable[[HelmPlanningCoordinator, date], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class HelmShoppingSensorDescription(SensorEntityDescription):
    """Describes a sensor derived from the shopping coordinator."""

    value_fn: Callable[[list[dict[str, Any]]], Any]
    attrs_fn: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None


def _today_items(
    coordinator: HelmPlanningCoordinator, today: date, planning_type: str
) -> list[dict[str, Any]]:
    """Return today's occurrences for one planning type."""
    stamp = today.isoformat()
    return [
        occurrence
        for occurrence in coordinator.occurrences(planning_type)
        if occurrence.get("date") == stamp
    ]


def _summarise(occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim occurrences down to what is useful in an attribute."""
    return [
        {
            "id": occurrence.get("id"),
            "type": occurrence.get("type"),
            "title": occurrence.get("title"),
            "date": occurrence.get("date"),
            "starts_at": occurrence.get("starts_at"),
            "all_day": occurrence.get("all_day"),
            "completed": occurrence_field(occurrence, "completed"),
        }
        for occurrence in occurrences
    ]


def _next_up(
    coordinator: HelmPlanningCoordinator, today: date
) -> dict[str, Any] | None:
    """Return the next occurrence that has not started yet."""
    now = coordinator.now()
    stamp = today.isoformat()
    for occurrence in coordinator.all_occurrences():
        day = occurrence.get("date")
        if not isinstance(day, str) or day < stamp:
            continue
        starts_at = occurrence.get("starts_at")
        if not starts_at:
            if day > stamp:
                return occurrence
            continue
        parsed = dt_util.parse_datetime(starts_at)
        if parsed is not None and parsed > now:
            return occurrence
    return None


def _next_up_title(coordinator: HelmPlanningCoordinator, today: date) -> str | None:
    """Return the title of the next occurrence, trimmed to fit a state."""
    occurrence = _next_up(coordinator, today)
    if occurrence is None:
        return None
    title = str(occurrence.get("title") or "")
    return title[:MAX_STATE_LENGTH] or None


PLANNING_SENSORS: tuple[HelmPlanningSensorDescription, ...] = (
    HelmPlanningSensorDescription(
        key="next_up",
        translation_key="next_up",
        value_fn=_next_up_title,
        attrs_fn=lambda coordinator, today: (
            {
                "occurrence": _next_up(coordinator, today),
                "timezone": coordinator.timezone_name,
            }
        ),
    ),
    HelmPlanningSensorDescription(
        key="agenda_today",
        translation_key="agenda_today",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="items",
        value_fn=lambda coordinator, today: sum(
            len(_today_items(coordinator, today, kind)) for kind in PLANNING_TYPES
        ),
        attrs_fn=lambda coordinator, today: {
            "items": _summarise(
                [
                    occurrence
                    for occurrence in coordinator.all_occurrences()
                    if occurrence.get("date") == today.isoformat()
                ]
            )
        },
    ),
    *(
        HelmPlanningSensorDescription(
            key=f"{endpoint}_today",
            translation_key=f"{endpoint}_today",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="items",
            value_fn=(
                lambda coordinator, today, kind=planning_type: len(
                    _today_items(coordinator, today, kind)
                )
            ),
            attrs_fn=(
                lambda coordinator, today, kind=planning_type: {
                    "items": _summarise(_today_items(coordinator, today, kind))
                }
            ),
        )
        for planning_type, endpoint in PLANNING_ENDPOINTS.items()
    ),
    *(
        HelmPlanningSensorDescription(
            key=f"{kind}s_outstanding",
            translation_key=f"{kind}s_outstanding",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="items",
            value_fn=(
                lambda coordinator, today, planning_type=kind: sum(
                    1
                    for occurrence in _today_items(coordinator, today, planning_type)
                    if not occurrence_field(occurrence, "completed")
                )
            ),
            attrs_fn=(
                lambda coordinator, today, planning_type=kind: {
                    "items": _summarise(
                        [
                            occurrence
                            for occurrence in _today_items(
                                coordinator, today, planning_type
                            )
                            if not occurrence_field(occurrence, "completed")
                        ]
                    )
                }
            ),
        )
        for kind in ("chore", "habit")
    ),
)


SHOPPING_SENSORS: tuple[HelmShoppingSensorDescription, ...] = (
    HelmShoppingSensorDescription(
        key="shopping_outstanding",
        translation_key="shopping_outstanding",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="items",
        value_fn=lambda items: sum(1 for item in items if not item.get("completed")),
        attrs_fn=lambda items: {
            "items": [item for item in items if not item.get("completed")]
        },
    ),
    HelmShoppingSensorDescription(
        key="shopping_total",
        translation_key="shopping_total",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="items",
        entity_registry_enabled_default=False,
        value_fn=len,
        attrs_fn=lambda items: {"items": items},
    ),
)


class HelmCredentialExpirySensor(SensorEntity):
    """When the credential lapses, straight from /me.

    The expiry is fixed at issue time, so this needs no coordinator and costs
    no requests.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "credential_expires"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: HelmConfigEntry) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_credential_expires"
        self._attr_native_value = entry.runtime_data.expires_at
        self._attr_device_info = helm_device_info(
            entry.entry_id, entry.data[CONF_BASE_URL]
        )
        self._attr_extra_state_attributes = {
            "credential": entry.runtime_data.credential.get("name"),
            "user": (entry.runtime_data.user or {}).get("name"),
            "team": (entry.runtime_data.team or {}).get("name"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HelmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Helm sensors the token's abilities allow."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = []

    # Only meaningful for credentials that actually expire.
    if runtime.expires_at is not None:
        entities.append(HelmCredentialExpirySensor(entry))

    if runtime.planning is not None:
        entities.extend(
            HelmPlanningSensor(runtime.planning, entry, description)
            for description in PLANNING_SENSORS
        )
    if runtime.shopping is not None:
        entities.extend(
            HelmShoppingSensor(runtime.shopping, entry, description)
            for description in SHOPPING_SENSORS
        )

    async_add_entities(entities)


class HelmPlanningSensor(HelmEntity, SensorEntity):
    """A sensor computed from the cached planning window."""

    _unrecorded_attributes = frozenset({"items", "occurrence"})
    coordinator: HelmPlanningCoordinator
    entity_description: HelmPlanningSensorDescription

    def __init__(
        self,
        coordinator: HelmPlanningCoordinator,
        entry: HelmConfigEntry,
        description: HelmPlanningSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(
            self.coordinator, self.coordinator.today()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the sensor attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(
            self.coordinator, self.coordinator.today()
        )


class HelmShoppingSensor(HelmEntity, SensorEntity):
    """A sensor computed from the shopping list."""

    _unrecorded_attributes = frozenset({"items"})
    coordinator: HelmShoppingCoordinator
    entity_description: HelmShoppingSensorDescription

    def __init__(
        self,
        coordinator: HelmShoppingCoordinator,
        entry: HelmConfigEntry,
        description: HelmShoppingSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the sensor attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data or [])

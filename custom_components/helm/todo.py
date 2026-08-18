"""The shared Helm shopping list, as a Home Assistant to-do list."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HelmError
from .const import (
    ABILITY_SHOPPING_WRITE,
    CONF_QTY_IN_SUMMARY,
    DEFAULT_QTY_IN_SUMMARY,
)
from .coordinator import HelmShoppingCoordinator
from .entity import HelmEntity

if TYPE_CHECKING:
    from . import HelmConfigEntry

# Matches a trailing quantity written as "x2", "×2" or "*2".
_QTY_SUFFIX = re.compile(r"\s*[x×*]\s*(\d{1,4})\s*$", re.IGNORECASE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HelmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the shopping list to-do entity."""
    coordinator = entry.runtime_data.shopping
    if coordinator is None:
        return
    async_add_entities([HelmShoppingListEntity(coordinator, entry)])


class HelmShoppingListEntity(HelmEntity, TodoListEntity):
    """One entity for the household's single shared shopping list."""

    coordinator: HelmShoppingCoordinator
    _attr_translation_key = "shopping_list"
    _unrecorded_attributes = frozenset({"items"})

    def __init__(
        self, coordinator: HelmShoppingCoordinator, entry: HelmConfigEntry
    ) -> None:
        """Initialise the to-do list."""
        super().__init__(coordinator, entry, "shopping_list")
        self._attr_supported_features = TodoListEntityFeature(0)
        if ABILITY_SHOPPING_WRITE in entry.runtime_data.abilities:
            self._attr_supported_features = (
                TodoListEntityFeature.CREATE_TODO_ITEM
                | TodoListEntityFeature.UPDATE_TODO_ITEM
                | TodoListEntityFeature.DELETE_TODO_ITEM
            )

    @property
    def _qty_in_summary(self) -> bool:
        """Whether quantities are shown and parsed in the item summary."""
        return bool(
            self._entry.options.get(CONF_QTY_IN_SUMMARY, DEFAULT_QTY_IN_SUMMARY)
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return the list, already ordered by the API."""
        if self.coordinator.data is None:
            return None
        return [self._to_todo_item(item) for item in self.coordinator.data]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the full records, since TodoItem cannot carry qty or category."""
        items = self.coordinator.data or []
        return {
            "items": items,
            "total": len(items),
            "completed": sum(1 for item in items if item.get("completed")),
        }

    def _raw_item(self, uid: str) -> dict[str, Any] | None:
        """Find the API record behind a to-do UID."""
        for item in self.coordinator.data or []:
            if str(item.get("id")) == uid:
                return item
        return None

    def _to_todo_item(self, item: dict[str, Any]) -> TodoItem:
        """Convert an API record into a to-do item."""
        summary = str(item.get("name") or "")
        qty = item.get("qty")
        if self._qty_in_summary and isinstance(qty, int) and qty > 1:
            summary = f"{summary} ×{qty}"
        return TodoItem(
            uid=str(item.get("id")),
            summary=summary,
            status=(
                TodoItemStatus.COMPLETED
                if item.get("completed")
                else TodoItemStatus.NEEDS_ACTION
            ),
        )

    def _split_summary(self, summary: str | None) -> tuple[str, int | None]:
        """Split a summary into a name and, optionally, a quantity."""
        text = (summary or "").strip()
        if not self._qty_in_summary:
            return text, None
        if match := _QTY_SUFFIX.search(text):
            name = _QTY_SUFFIX.sub("", text).strip()
            if name:
                return name, int(match.group(1))
        return text, None

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an item to the shopping list."""
        name, qty = self._split_summary(item.summary)
        if not name:
            raise HomeAssistantError("A shopping list item needs a name")

        fields: dict[str, Any] = {"name": name[:255]}
        if qty is not None:
            fields["qty"] = qty

        try:
            await self.coordinator.client.async_create_shopping_item(
                fields, idempotency_key=f"ha-{uuid4().hex}"
            )
        except HelmError as err:
            raise HomeAssistantError(f"Could not add '{name}': {err.message}") from err

        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update an item, keeping a tick-only change to a bare toggle."""
        if item.uid is None:
            raise HomeAssistantError("Cannot update an item without an ID")

        existing = self._raw_item(item.uid)
        completed = item.status == TodoItemStatus.COMPLETED
        fields: dict[str, Any] = {}

        if existing is None or bool(existing.get("completed")) != completed:
            fields["completed"] = completed

        name, qty = self._split_summary(item.summary)
        if name and (existing is None or existing.get("name") != name):
            fields["name"] = name[:255]
        if qty is not None and (existing is None or existing.get("qty") != qty):
            fields["qty"] = qty

        if not fields:
            return

        try:
            await self.coordinator.client.async_update_shopping_item(
                int(item.uid), fields
            )
        except HelmError as err:
            raise HomeAssistantError(
                f"Could not update '{item.summary}': {err.message}"
            ) from err

        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete one or more items."""
        for uid in uids:
            try:
                await self.coordinator.client.async_delete_shopping_item(int(uid))
            except HelmError as err:
                raise HomeAssistantError(
                    f"Could not delete item {uid}: {err.message}"
                ) from err

        await self.coordinator.async_request_refresh()

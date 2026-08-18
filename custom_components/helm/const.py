"""Constants for the Helm integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "helm"

DEFAULT_NAME: Final = "Helm"

CONF_BASE_URL: Final = "base_url"
CONF_DAYS_AHEAD: Final = "days_ahead"
CONF_DAYS_PAST: Final = "days_past"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_QTY_IN_SUMMARY: Final = "qty_in_summary"
CONF_ABILITIES: Final = "abilities"
CONF_USER: Final = "user"
CONF_TEAM: Final = "team"
CONF_TIMEZONE: Final = "timezone"
CONF_CREDENTIAL: Final = "credential"

DEFAULT_DAYS_AHEAD: Final = 7
DEFAULT_DAYS_PAST: Final = 0
DEFAULT_UPDATE_INTERVAL: Final = 5  # minutes
DEFAULT_QTY_IN_SUMMARY: Final = True

MIN_UPDATE_INTERVAL: Final = 1
MAX_UPDATE_INTERVAL: Final = 1440
MAX_DAYS_AHEAD: Final = 30
MAX_DAYS_PAST: Final = 30

# The API rejects ranges longer than 31 days. Chunk inclusively: a window of
# 31 days is `start` .. `start + 30 days`.
MAX_RANGE_DAYS: Final = 31

# How long before a credential lapses to start warning about it.
EXPIRY_WARNING_DAYS: Final = 14
ISSUE_TOKEN_EXPIRING: Final = "token_expiring"

ABILITY_PLANNING_READ: Final = "planning:read"
ABILITY_SHOPPING_READ: Final = "shopping:read"
ABILITY_SHOPPING_WRITE: Final = "shopping:write"

# Planning occurrence type -> API endpoint path segment.
PLANNING_ENDPOINTS: Final[dict[str, str]] = {
    "meal": "meals",
    "exercise": "exercises",
    "event": "events",
    "chore": "chores",
    "habit": "habits",
}

PLANNING_TYPES: Final = tuple(PLANNING_ENDPOINTS)

SHOPPING_ITEMS_PATH: Final = "/shopping-list/items"
ME_PATH: Final = "/me"

# Fallback length for a timed occurrence with no end and no duration.
DEFAULT_EVENT_MINUTES: Final = 60

SERVICE_ADD_SHOPPING_ITEM: Final = "add_shopping_item"
SERVICE_UPDATE_SHOPPING_ITEM: Final = "update_shopping_item"
SERVICE_DELETE_SHOPPING_ITEM: Final = "delete_shopping_item"
SERVICE_GET_PLANNING: Final = "get_planning"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_ENTITY_ID: Final = "entity_id"
ATTR_ITEM_ID: Final = "item_id"
ATTR_NAME: Final = "name"
ATTR_QTY: Final = "qty"
ATTR_URL: Final = "url"
ATTR_CATEGORY_ID: Final = "shopping_list_type_id"
ATTR_COMPLETED: Final = "completed"
ATTR_SORT_ORDER: Final = "sort_order"
ATTR_VISIBLE_TO_EXTENDED: Final = "visible_to_extended"
ATTR_IDEMPOTENCY_KEY: Final = "idempotency_key"
ATTR_TYPES: Final = "types"
ATTR_FROM: Final = "from"
ATTR_TO: Final = "to"

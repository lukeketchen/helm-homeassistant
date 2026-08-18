"""Tests for the bundled Lovelace card and entity-targeted services."""

from __future__ import annotations

from pathlib import Path
import re
from unittest.mock import patch

from homeassistant.components.frontend import (
    DATA_EXTRA_JS_URL_ES5,
    DATA_EXTRA_MODULE_URL,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from custom_components.helm import CARD_URL
from custom_components.helm.const import DOMAIN

from .conftest import BASE_URL, setup_helm

CARD = Path("custom_components/helm/www/helm-shopping-card.js")
ITEMS_URL = f"{BASE_URL}/shopping-list/items"


def test_card_ships_with_the_integration() -> None:
    """The card lives inside the integration, so HACS needs no second repo."""
    assert CARD.is_file()
    source = CARD.read_text(encoding="utf-8")
    assert 'customElements.define("helm-shopping-card"' in source
    assert 'customElements.define("helm-shopping-card-editor"' in source
    assert "window.customCards" in source


def test_card_has_no_external_dependencies() -> None:
    """The card must not import anything at runtime; it is served offline."""
    source = CARD.read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s", source, re.MULTILINE)
    assert "https://unpkg" not in source
    assert "cdn." not in source


def test_card_escapes_item_content() -> None:
    """Item names come from the API, so they are escaped before interpolation."""
    source = CARD.read_text(encoding="utf-8")
    assert "const escapeHtml" in source
    # Every interpolation of a name or URL goes through escapeHtml.
    assert "${escapeHtml(item.name)}" in source
    assert "${escapeHtml(item.url)}" in source


async def test_card_is_registered_with_the_frontend(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Setting up the integration serves the card and adds the JS url."""
    assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    urls = set(hass.data[DATA_EXTRA_MODULE_URL].urls) | set(
        hass.data[DATA_EXTRA_JS_URL_ES5].urls
    )
    assert any(url.startswith(CARD_URL) for url in urls), urls
    # The url carries a version, so browsers pick up a new card after an upgrade.
    assert any("?v=" in url for url in urls if url.startswith(CARD_URL))


async def test_service_accepts_an_entity_instead_of_an_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The card targets its configured entity rather than a config entry ID."""
    await setup_helm(hass, aioclient_mock, config_entry)
    aioclient_mock.patch(f"{ITEMS_URL}/91", json={"data": {"id": 91, "qty": 3}})

    await hass.services.async_call(
        DOMAIN,
        "update_shopping_item",
        {"entity_id": "todo.helm_shopping_list", "item_id": 91, "qty": 3},
        blocking=True,
    )

    patch = next(
        call
        for call in reversed(aioclient_mock.mock_calls)
        if call[0].lower() == "patch"
    )
    assert patch[1].path.endswith("/shopping-list/items/91")


async def test_service_rejects_a_foreign_entity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """An entity from another integration cannot be used as a target."""
    await setup_helm(hass, aioclient_mock, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_shopping_item",
            {"entity_id": "todo.someone_elses_list", "item_id": 91},
            blocking=True,
        )


async def test_service_requires_some_target(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Naming neither target is rejected by the schema."""
    await setup_helm(hass, aioclient_mock, config_entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "delete_shopping_item", {"item_id": 91}, blocking=True
        )


async def test_entities_still_load_without_the_frontend(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A card registration failure must not take the entities down with it."""
    with patch(
        "custom_components.helm._async_register_card",
        side_effect=RuntimeError("no frontend here"),
    ):
        await setup_helm(hass, aioclient_mock, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("todo.helm_shopping_list") is not None


def test_no_real_hostname_is_baked_in() -> None:
    """The integration must not ship anyone's actual Helm server address.

    The base URL is asked for at setup instead, so this repository can be
    public without advertising a private deployment.
    """
    root = Path("custom_components/helm")
    suspicious = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".json", ".yaml", ".js"}:
            continue
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in re.finditer(r"https?://([\w.-]+)", line):
                host = match.group(1)
                # github.com links (docs, issue tracker) and example hosts are fine.
                if host.endswith("github.com") or "example.com" in host:
                    continue
                suspicious.append(f"{path}:{line_no} {host}")
    assert not suspicious, f"Real hostnames found: {suspicious}"

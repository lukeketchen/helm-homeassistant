"""Tests for the Helm config flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.helm.const import CONF_ABILITIES, CONF_BASE_URL, DOMAIN

from .conftest import BASE_URL, TOKEN, mock_api


async def _start(hass: HomeAssistant):
    """Open the user step."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_full_flow_records_every_ability(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A token with all three abilities creates an entry listing them."""
    mock_api(aioclient_mock, today=dt_util.now().date())

    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BASE_URL] == BASE_URL
    assert result["data"][CONF_API_TOKEN] == TOKEN
    assert result["data"][CONF_ABILITIES] == [
        "planning:read",
        "shopping:read",
        "shopping:write",
    ]


async def test_read_only_shopping_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A token without shopping:write is detected by the PATCH probe."""
    mock_api(aioclient_mock, today=dt_util.now().date(), planning=False, write=False)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ABILITIES] == ["shopping:read"]


async def test_invalid_token_format_is_caught_locally(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """A token that is not a helm_ token never reaches the network."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: "nope"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_API_TOKEN: "invalid_token_format"}


async def test_invalid_auth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A revoked token surfaces as invalid_auth."""
    aioclient_mock.get(
        f"{BASE_URL}/me",
        status=401,
        json={"error": {"code": "api_token_invalid", "message": "Nope."}},
    )

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_token_with_no_abilities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A token that can authenticate but do nothing is rejected."""
    mock_api(
        aioclient_mock,
        today=dt_util.now().date(),
        planning=False,
        shopping=False,
        write=False,
    )

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_abilities"}


async def test_duplicate_server_is_aborted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    enable_custom_integrations,
) -> None:
    """The same Helm server cannot be added twice."""
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock, today=dt_util.now().date())

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    enable_custom_integrations,
) -> None:
    """Reauth swaps in a new token and reloads the entry."""
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock, today=dt_util.now().date())

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    new_token = "helm_" + "b" * 68
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: new_token}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_TOKEN] == new_token

"""Tests for the /me endpoint: identity, timezone seeding and expiry warnings."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.helm.const import (
    CONF_ABILITIES,
    CONF_BASE_URL,
    CONF_CREDENTIAL,
    CONF_TEAM,
    CONF_TIMEZONE,
    CONF_USER,
    DOMAIN,
    ISSUE_TOKEN_EXPIRING,
)

from .conftest import (
    ALL_ABILITIES,
    BASE_URL,
    HOUSEHOLD_TZ,
    TOKEN,
    _entry,
    me_payload,
    mock_api,
    setup_helm,
)


async def _run_flow(hass: HomeAssistant):
    """Take the user step to completion."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )


async def test_me_populates_the_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """Identity, timezone and credential details are all stored from /me."""
    await hass.config.async_set_time_zone(HOUSEHOLD_TZ)
    mock_api(aioclient_mock, today=dt_util.now().date())

    result = await _run_flow(hass)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_ABILITIES] == [
        "planning:read",
        "shopping:read",
        "shopping:write",
    ]
    assert data[CONF_USER]["name"] == "Luke"
    assert data[CONF_TEAM]["name"] == "Ketchen"
    assert data[CONF_TIMEZONE] == HOUSEHOLD_TZ
    assert data[CONF_CREDENTIAL]["name"] == "Home Assistant"
    # Titled after the household and the credential label.
    assert result["title"] == "Ketchen (Home Assistant)"


async def test_me_makes_the_unique_id_per_user(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """Two household members can each add their own credential."""
    await hass.config.async_set_time_zone(HOUSEHOLD_TZ)
    mock_api(aioclient_mock, today=dt_util.now().date())

    result = await _run_flow(hass)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(result["result"].entry_id)
    assert entry.unique_id == "helm.test-1-4"


async def test_falls_back_to_probing_without_me(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A server predating /me still sets up, via the ability probe."""
    await hass.config.async_set_time_zone(HOUSEHOLD_TZ)
    mock_api(aioclient_mock, today=dt_util.now().date(), me_status=404)

    result = await _run_flow(hass)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ABILITIES] == [
        "planning:read",
        "shopping:read",
        "shopping:write",
    ]
    # No identity available, so the unique ID falls back to the host alone.
    entry = hass.config_entries.async_get_entry(result["result"].entry_id)
    assert entry.unique_id == "helm.test"
    assert result["data"][CONF_TIMEZONE] is None


async def test_timezone_is_seeded_before_the_first_poll(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The household timezone is known up front, not after the first response."""
    await setup_helm(hass, aioclient_mock, config_entry)

    coordinator = config_entry.runtime_data.planning
    assert coordinator.timezone_name == HOUSEHOLD_TZ
    assert coordinator.timezone is not None


async def test_no_expiry_means_no_sensor_and_no_repair(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A credential that never lapses raises nothing."""
    await setup_helm(hass, aioclient_mock, config_entry)

    assert hass.states.get("sensor.helm_credential_expires") is None
    issues = ir.async_get(hass).issues
    assert not any(domain == DOMAIN for domain, _ in issues)


async def test_expiring_credential_warns(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    expiring_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A credential lapsing in three days gets a sensor and a repair issue."""
    await setup_helm(hass, aioclient_mock, expiring_entry)

    state = hass.states.get("sensor.helm_credential_expires")
    assert state is not None
    assert state.attributes["device_class"] == "timestamp"
    assert state.attributes["credential"] == "Home Assistant"

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_TOKEN_EXPIRING}_{expiring_entry.entry_id}"
    )
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders["credential"] == "Home Assistant"


async def test_distant_expiry_does_not_warn(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    enable_custom_integrations,
) -> None:
    """A credential good for another year is left alone."""
    far_off = (dt_util.utcnow() + timedelta(days=365)).isoformat()
    entry = _entry(ALL_ABILITIES, expires_at=far_off)
    await setup_helm(hass, aioclient_mock, entry)

    assert hass.states.get("sensor.helm_credential_expires") is not None
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_TOKEN_EXPIRING}_{entry.entry_id}"
    )
    assert issue is None


async def test_reauth_refreshes_identity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A new token re-reads /me, so changed abilities are picked up."""
    await hass.config.async_set_time_zone(HOUSEHOLD_TZ)
    config_entry.add_to_hass(hass)
    mock_api(
        aioclient_mock,
        today=dt_util.now().date(),
        me=me_payload(abilities=["shopping:read"]),
    )

    result = await config_entry.start_reauth_flow(hass)
    new_token = "helm_" + "c" * 68
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: new_token}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_TOKEN] == new_token
    assert config_entry.data[CONF_ABILITIES] == ["shopping:read"]

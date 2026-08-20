"""Async client for the Helm household API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
import logging
from typing import Any, Final

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from homeassistant.util.json import json_loads

from .const import COMPLETABLE_TYPES, MAX_RANGE_DAYS, ME_PATH, SHOPPING_ITEMS_PATH

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = ClientTimeout(total=30)

# A record ID that will never exist, used to probe for shopping:write without
# touching real data. The ability check runs before the record lookup, so a
# 404 means "you may write, that item just isn't there".
_WRITE_PROBE_ID: Final = 2_147_483_647


class HelmError(Exception):
    """Base error raised by the Helm client."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        fields: dict[str, list[str]] | None = None,
    ) -> None:
        """Initialise the error."""
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.fields = fields or {}


class HelmConnectionError(HelmError):
    """The API could not be reached."""


class HelmAuthError(HelmError):
    """The token is missing, malformed, invalid, revoked or expired."""


class HelmAbilityError(HelmError):
    """The token is valid but lacks the ability for this call."""


class HelmNotFoundError(HelmError):
    """No such record, or it belongs to another team."""


class HelmValidationError(HelmError):
    """The request body failed validation."""


class HelmRateLimitError(HelmError):
    """Too many requests for this credential or IP."""

    def __init__(
        self, message: str, *, retry_after: int | None = None, **kwargs: Any
    ) -> None:
        """Initialise the error, keeping the Retry-After hint."""
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


def date_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split an inclusive date range into windows the API will accept."""
    if end < start:
        start, end = end, start
    cursor = start
    step = timedelta(days=MAX_RANGE_DAYS - 1)
    while cursor <= end:
        chunk_end = min(cursor + step, end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _raise_for_payload(response: ClientResponse, payload: Any) -> None:
    """Translate an error response into the matching exception."""
    error = {}
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
    code = error.get("code")
    message = error.get("message") or f"HTTP {response.status} from the Helm API"
    fields = error.get("fields") if isinstance(error.get("fields"), dict) else None
    status = response.status

    common = {"code": code, "status": status, "fields": fields}

    if status == 401:
        raise HelmAuthError(message, **common)
    if status == 403:
        if code == "insufficient_ability":
            raise HelmAbilityError(message, **common)
        raise HelmAbilityError(message, **common)
    if status == 404:
        raise HelmNotFoundError(message, **common)
    if status == 422:
        raise HelmValidationError(message, **common)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        raise HelmRateLimitError(
            message,
            retry_after=int(retry_after)
            if retry_after and retry_after.isdigit()
            else None,
            **common,
        )
    raise HelmError(message, **common)


class HelmClient:
    """Minimal wrapper around the Helm HTTP API."""

    def __init__(self, session: ClientSession, base_url: str, token: str) -> None:
        """Initialise the client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    @property
    def base_url(self) -> str:
        """Return the configured base URL."""
        return self._base_url

    def update_token(self, token: str) -> None:
        """Swap in a freshly issued token, as used by the reauth flow."""
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """Perform a request and return the decoded body, or None for 204."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                raw = await response.text()
                payload: Any = None
                if raw:
                    try:
                        payload = json_loads(raw)
                    except ValueError:
                        payload = None

                if response.status >= 400:
                    _raise_for_payload(response, payload)

                if response.status == 204:
                    return None
                if payload is None:
                    raise HelmError(
                        f"The Helm API returned a non-JSON body for {method} {path}"
                    )
                return payload
        except TimeoutError as err:
            raise HelmConnectionError(f"Timed out talking to {url}") from err
        except ClientError as err:
            raise HelmConnectionError(f"Could not reach {url}: {err}") from err

    async def async_get_me(self) -> dict[str, Any]:
        """Return who this credential belongs to and what it may do.

        Needs no ability of its own, so a shopping-only credential can still
        discover that it is shopping-only.
        """
        payload = await self._request("GET", ME_PATH)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HelmError("The /me response had no data object")
        return data

    async def async_get_planning(
        self, endpoint: str, start: date, end: date
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return occurrences for a range, chunked to fit the 31 day limit."""
        occurrences: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        for chunk_start, chunk_end in date_chunks(start, end):
            payload = await self._request(
                "GET",
                f"/{endpoint}",
                params={"from": chunk_start.isoformat(), "to": chunk_end.isoformat()},
            )
            data = payload.get("data")
            if isinstance(data, list):
                occurrences.extend(item for item in data if isinstance(item, dict))
            if not meta and isinstance(payload.get("meta"), dict):
                meta = payload["meta"]
        return occurrences, meta

    async def async_set_occurrence_completed(
        self, kind: str, record_id: int | str, day: date, completed: bool
    ) -> dict[str, Any]:
        """Tick a chore or habit occurrence off, or clear it.

        `record_id` is the underlying record - `source.id` on an occurrence -
        not the occurrence's own composite ID. Idempotent in both directions,
        so a retry is safe without checking the current state first.
        """
        endpoint = COMPLETABLE_TYPES.get(kind)
        if endpoint is None:
            raise HelmError(f"{kind} occurrences cannot be completed")

        payload = await self._request(
            "PATCH",
            f"/{endpoint}/{record_id}/occurrences/{day.isoformat()}",
            body={"completed": completed},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else {}

    async def async_get_shopping_items(self) -> list[dict[str, Any]]:
        """Return every shopping list item visible to this token."""
        payload = await self._request("GET", SHOPPING_ITEMS_PATH)
        data = payload.get("data")
        return (
            [item for item in data if isinstance(item, dict)]
            if isinstance(data, list)
            else []
        )

    async def async_create_shopping_item(
        self, fields: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Create a shopping list item and return it."""
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        payload = await self._request(
            "POST", SHOPPING_ITEMS_PATH, body=fields, extra_headers=headers
        )
        return payload.get("data", {}) if isinstance(payload, dict) else {}

    async def async_update_shopping_item(
        self, item_id: int, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Patch a shopping list item and return the updated record."""
        payload = await self._request(
            "PATCH", f"{SHOPPING_ITEMS_PATH}/{item_id}", body=fields
        )
        return payload.get("data", {}) if isinstance(payload, dict) else {}

    async def async_delete_shopping_item(self, item_id: int) -> None:
        """Delete a shopping list item."""
        await self._request("DELETE", f"{SHOPPING_ITEMS_PATH}/{item_id}")

    async def async_identify(self, today: date) -> dict[str, Any]:
        """Describe this credential, using /me where the server offers it.

        Returns the /me data object. Servers predating /me fall back to
        probing, which yields abilities only.
        """
        try:
            return await self.async_get_me()
        except HelmNotFoundError:
            _LOGGER.debug("This Helm server has no /me endpoint; probing instead")
        except HelmAbilityError:
            # /me is meant to be ungated, but never let that block setup.
            _LOGGER.debug("/me was refused; probing instead")

        return {"abilities": sorted(await self.async_probe_abilities(today))}

    async def async_probe_abilities(self, today: date) -> set[str]:
        """Work out which abilities this token carries without /me.

        Each ability is probed with a call that is harmless on its own. Auth
        and transport failures propagate; only `insufficient_ability` is
        swallowed.
        """
        from .const import (  # noqa: PLC0415 - avoids a circular import at module load
            ABILITY_PLANNING_READ,
            ABILITY_SHOPPING_READ,
            ABILITY_SHOPPING_WRITE,
        )

        abilities: set[str] = set()

        try:
            await self._request(
                "GET",
                "/schedule",
                params={"from": today.isoformat(), "to": today.isoformat()},
            )
        except HelmAbilityError:
            _LOGGER.debug("Token has no %s ability", ABILITY_PLANNING_READ)
        else:
            abilities.add(ABILITY_PLANNING_READ)

        try:
            await self.async_get_shopping_items()
        except HelmAbilityError:
            _LOGGER.debug("Token has no %s ability", ABILITY_SHOPPING_READ)
        else:
            abilities.add(ABILITY_SHOPPING_READ)

        if ABILITY_SHOPPING_READ in abilities:
            try:
                await self.async_update_shopping_item(
                    _WRITE_PROBE_ID, {"completed": True}
                )
            except HelmAbilityError:
                _LOGGER.debug("Token has no %s ability", ABILITY_SHOPPING_WRITE)
            except (HelmNotFoundError, HelmValidationError):
                # Got past the ability gate, so the write ability is present.
                abilities.add(ABILITY_SHOPPING_WRITE)
            else:
                abilities.add(ABILITY_SHOPPING_WRITE)

        return abilities

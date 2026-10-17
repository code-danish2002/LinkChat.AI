"""Checks configured model ids against what each provider actually serves.

Hosted free tiers rotate their catalogues often, so a model id that worked
last month may 404 today. The validator asks every active provider for its
model list once at startup and records which configured entries are really
available. Results are advisory: routing still tries a model marked missing,
because a provider may omit it from the listing yet still serve it.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.providers.llm.registry import ModelRegistry, ResolvedProvider

log = logging.getLogger(__name__)


async def fetch_provider_models(
    http: httpx.AsyncClient, provider: ResolvedProvider, timeout: float = 10.0
) -> set[str] | None:
    """Model ids a provider reports, or None when the listing cannot be read."""
    try:
        response = await http.get(
            f"{provider.base_url}/models",
            headers={"Authorization": f"Bearer {provider.api_key}", **provider.extra_headers},
            timeout=timeout,
        )
        if response.status_code >= 400:
            log.info("%s model listing returned HTTP %s", provider.name, response.status_code)
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("%s model listing unavailable: %s", provider.name, exc.__class__.__name__)
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    return {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}


async def validate_pool(
    registry: ModelRegistry, http: httpx.AsyncClient, timeout: float = 10.0
) -> dict[str, bool]:
    """Map `provider/model` -> served upstream, for every active model."""
    active = registry.active_models() + registry.active_transcription_models()
    providers: dict[str, ResolvedProvider] = {p.name: p for _, p in active}
    if not providers:
        return {}

    names = list(providers)
    listings = await asyncio.gather(
        *(fetch_provider_models(http, providers[name], timeout) for name in names)
    )
    served = dict(zip(names, listings, strict=True))

    availability: dict[str, bool] = {}
    missing: list[str] = []
    for spec, _ in active:
        catalogue = served.get(spec.provider)
        if catalogue is None:
            continue  # listing unreadable: stay silent rather than guess
        available = spec.model in catalogue
        availability[spec.id] = available
        if not available:
            missing.append(spec.id)

    if missing:
        log.warning(
            "%d configured model(s) are not served by their provider and will be skipped "
            "after the first failure: %s",
            len(missing),
            ", ".join(sorted(missing)),
        )
    else:
        checked = sum(1 for name in names if served.get(name) is not None)
        log.info("model pool verified against %d provider listing(s)", checked)
    return availability

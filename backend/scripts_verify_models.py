#!/usr/bin/env python3
"""Report configured models that their provider no longer serves.

Free tiers rotate their catalogues often, so entries in config/models.yaml go
stale. Run this after adding a key, or whenever answers start failing over:

    .venv/bin/python scripts_verify_models.py
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from app.core.config import get_settings
from app.providers.llm.registry import ModelRegistry
from app.providers.llm.validation import fetch_provider_models


async def main() -> int:
    settings = get_settings()
    registry = ModelRegistry.from_yaml(settings.models_config_path)
    active = registry.active_models() + registry.active_transcription_models()
    if not active:
        print("No provider has credentials. Add a key to backend/.env first.")
        return 1

    providers = {provider.name: provider for _, provider in active}
    async with httpx.AsyncClient(timeout=20.0) as http:
        listings = await asyncio.gather(
            *(fetch_provider_models(http, provider) for provider in providers.values())
        )
    served = dict(zip(providers, listings, strict=True))

    stale: list[str] = []
    for spec, _ in active:
        catalogue = served.get(spec.provider)
        if catalogue is None:
            print(f"?  {spec.id:55} provider listing unreadable")
            continue
        if spec.model in catalogue:
            print(f"ok {spec.id:55} served")
        else:
            print(f"XX {spec.id:55} NOT SERVED")
            stale.append(spec.id)

    inactive = {s.provider for s in registry.models} - set(providers)
    if inactive:
        print(f"\nproviders without a key (skipped): {', '.join(sorted(inactive))}")
    if stale:
        print(f"\n{len(stale)} stale entr{'y' if len(stale) == 1 else 'ies'} in {settings.models_config_path}:")
        for model_id in stale:
            print(f"  - {model_id}")
        print("Replace them with ids the provider currently lists.")
        return 1
    print("\nEvery configured model is served by its provider.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

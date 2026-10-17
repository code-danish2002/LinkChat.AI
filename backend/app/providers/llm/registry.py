"""Model pool definition, loaded from config/models.yaml.

A provider is *active* when its credentials are present. Only models of
active providers are candidates for routing, so the pool adapts to whatever
keys the operator has configured without editing the YAML.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str | None = None
    base_url_env: str | None = None
    opt_in_env: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)

    def resolve(self) -> ResolvedProvider | None:
        """Return connection details if this provider is usable in the current environment."""
        if self.opt_in_env and os.getenv(self.opt_in_env, "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return None
        api_key = os.getenv(self.api_key_env, "").strip() if self.api_key_env else ""
        if self.api_key_env and not api_key:
            return None
        base_url = self.base_url
        if self.base_url_env and os.getenv(self.base_url_env, "").strip():
            base_url = os.getenv(self.base_url_env, "").strip()
        return ResolvedProvider(
            name=self.name,
            base_url=base_url.rstrip("/"),
            api_key=api_key or "not-needed",
            extra_headers=dict(self.extra_headers),
        )


@dataclass(frozen=True)
class ResolvedProvider:
    name: str
    base_url: str
    api_key: str
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    display_name: str
    priority: int = 100
    max_output_tokens: int | None = None
    #: Eligible for bulk multi-request work such as condensing a long document.
    #: Set false for models that answer well but slowly: a summary makes many
    #: requests, so one slow member drags out the whole run.
    bulk: bool = True

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"


class ModelRegistry:
    def __init__(
        self,
        providers: dict[str, ProviderSpec],
        models: list[ModelSpec],
        transcription: list[ModelSpec] | None = None,
    ):
        self._providers = providers
        self._models = sorted(models, key=lambda m: (m.priority, m.id))
        self._transcription = sorted(transcription or [], key=lambda m: (m.priority, m.id))
        unknown = {m.provider for m in [*models, *(transcription or [])]} - set(providers)
        if unknown:
            raise ValueError(f"models reference unknown providers: {sorted(unknown)}")

    @classmethod
    def from_yaml(cls, path: Path) -> ModelRegistry:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        providers = {
            name: ProviderSpec(
                name=name,
                base_url=str(spec["base_url"]),
                api_key_env=spec.get("api_key_env") or None,
                base_url_env=spec.get("base_url_env") or None,
                opt_in_env=spec.get("opt_in_env") or None,
                extra_headers={str(k): str(v) for k, v in (spec.get("extra_headers") or {}).items()},
            )
            for name, spec in (raw.get("providers") or {}).items()
        }
        models = [
            ModelSpec(
                provider=str(item["provider"]),
                model=str(item["model"]),
                display_name=str(item.get("display_name") or item["model"]),
                priority=int(item.get("priority", 100)),
                max_output_tokens=item.get("max_output_tokens"),
                bulk=bool(item.get("bulk", True)),
            )
            for item in (raw.get("models") or [])
        ]
        if not models:
            raise ValueError(f"no models defined in {path}")
        transcription = [
            ModelSpec(
                provider=str(item["provider"]),
                model=str(item["model"]),
                display_name=str(item.get("display_name") or item["model"]),
                priority=int(item.get("priority", 100)),
            )
            for item in (raw.get("transcription") or [])
        ]
        return cls(providers, models, transcription)

    @property
    def models(self) -> list[ModelSpec]:
        return list(self._models)

    @property
    def transcription_models(self) -> list[ModelSpec]:
        return list(self._transcription)

    @property
    def providers(self) -> dict[str, ProviderSpec]:
        return dict(self._providers)

    def resolve_provider(self, name: str) -> ResolvedProvider | None:
        return self._providers[name].resolve()

    def active_models(self) -> list[tuple[ModelSpec, ResolvedProvider]]:
        """Chat models whose provider has credentials, in priority order."""
        return self._activate(self._models)

    def active_transcription_models(self) -> list[tuple[ModelSpec, ResolvedProvider]]:
        """Speech-to-text models whose provider has credentials, in priority order."""
        return self._activate(self._transcription)

    def _activate(self, specs: list[ModelSpec]) -> list[tuple[ModelSpec, ResolvedProvider]]:
        resolved: dict[str, ResolvedProvider | None] = {}
        result: list[tuple[ModelSpec, ResolvedProvider]] = []
        for spec in specs:
            if spec.provider not in resolved:
                resolved[spec.provider] = self.resolve_provider(spec.provider)
            provider = resolved[spec.provider]
            if provider is not None:
                result.append((spec, provider))
        return result

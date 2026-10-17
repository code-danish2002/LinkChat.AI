"use client";

import { useState } from "react";

import { Icon } from "@/components/ui/Icon";
import { useModelPool } from "@/hooks/useModelPool";
import type { ModelStatus } from "@/lib/api/types";

const STATE_STYLES: Record<ModelStatus["state"], { dot: string; label: string }> = {
  closed: { dot: "bg-emerald-500", label: "healthy" },
  half_open: { dot: "bg-amber-500", label: "probing" },
  open: { dot: "bg-rose-500", label: "cooling down" },
  inactive: { dot: "bg-slate-300 dark:bg-slate-600", label: "no key" },
};

function describe(model: ModelStatus): string {
  if (model.state === "open" && model.retry_in_seconds !== null) {
    return `retry in ${Math.ceil(model.retry_in_seconds)}s`;
  }
  if (model.active && model.available_upstream === false) return "retired by provider";
  const base = STATE_STYLES[model.state].label;
  if (model.state === "closed" && model.average_latency_ms !== null) {
    return `${base} · ${(model.average_latency_ms / 1000).toFixed(1)}s`;
  }
  return base;
}

export function ModelPool({ compact = false }: { compact?: boolean }) {
  const { pool, error } = useModelPool();
  const [open, setOpen] = useState(!compact);

  if (error) return <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>;
  if (!pool) return <p className="text-xs text-slate-400">Loading model pool…</p>;

  const healthy = pool.models.filter((model) => model.active && model.state !== "open").length;
  const retired = pool.models.filter((model) => model.active && model.available_upstream === false).length;
  const tone = pool.active_count === 0 ? "text-rose-600 dark:text-rose-400" : "text-slate-600 dark:text-slate-300";
  const headline = pool.active_count === 0 ? "bg-rose-500" : healthy > 0 ? "bg-emerald-500" : "bg-amber-500";

  return (
    <div className="rounded-2xl bg-white ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-2 p-3 text-left"
      >
        <span className={`flex items-center gap-2 text-sm font-medium ${tone}`}>
          <span className={`h-2 w-2 rounded-full ${headline}`} />
          Model pool · {healthy}/{pool.active_count} healthy
        </span>
        <Icon name="chevron" className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="border-t border-slate-100 dark:border-slate-800">
          {pool.active_count === 0 && (
            <p className="p-3 text-xs text-rose-600 dark:text-rose-400">
              No provider is configured. Add an API key (Groq, Cerebras, OpenRouter, Together, Hugging Face) or enable
              Ollama in the backend .env file.
            </p>
          )}
          <ul className="scrollbar-thin max-h-72 divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800">
            {pool.models.map((model) => (
              <li
                key={model.id}
                className={`flex items-center gap-2 px-3 py-1.5 text-xs ${model.active ? "" : "opacity-50"}`}
                title={model.last_error ?? model.id}
              >
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATE_STYLES[model.state].dot}`} />
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium text-slate-800 dark:text-slate-100">{model.display_name}</span>
                  <span className="text-slate-400"> · {model.provider}</span>
                </span>
                <span className="shrink-0 text-slate-500 dark:text-slate-400">{describe(model)}</span>
              </li>
            ))}
          </ul>
          <p className="border-t border-slate-100 p-2 text-[11px] text-slate-400 dark:border-slate-800">
            {pool.total_count} models configured. Tried in order; a failing one is skipped until its cooldown ends.
            {retired > 0 && ` ${retired} no longer served by its provider: edit config/models.yaml.`}
          </p>
        </div>
      )}
    </div>
  );
}

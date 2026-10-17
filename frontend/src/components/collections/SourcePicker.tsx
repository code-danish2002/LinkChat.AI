"use client";

import { useState } from "react";

import { KindIcon } from "@/components/sources/KindIcon";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { Source } from "@/lib/api/types";
import { hostOf } from "@/lib/format";

/** Picks sources to add to a collection, excluding the ones already in it. */
export function SourcePicker({
  sources,
  excluded,
  onAdd,
}: {
  sources: Source[];
  excluded: string[];
  onAdd: (sourceId: string) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const available = sources.filter(
    (source) =>
      !excluded.includes(source.id) &&
      source.status !== "failed" &&
      (source.title.toLowerCase().includes(query.toLowerCase()) ||
        source.url.toLowerCase().includes(query.toLowerCase())),
  );

  async function add(id: string) {
    setBusy(id);
    try {
      await onAdd(id);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-2xl bg-white ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-2 p-3 text-left text-sm font-medium"
      >
        <span className="inline-flex items-center gap-2">
          <Icon name="plus" className="h-4 w-4 text-indigo-500" /> Add sources
        </span>
        <Icon name="chevron" className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="border-t border-slate-100 p-3 dark:border-slate-800">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter by title or link…"
            className="mb-2 h-9 w-full rounded-lg bg-slate-50 px-3 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500 dark:bg-slate-800 dark:ring-slate-700"
          />
          {available.length === 0 ? (
            <p className="py-2 text-xs text-slate-500 dark:text-slate-400">
              Nothing left to add. Create a source from the home page first.
            </p>
          ) : (
            <ul className="scrollbar-thin max-h-64 space-y-1 overflow-y-auto">
              {available.map((source) => (
                <li key={source.id} className="flex items-center gap-2 rounded-lg p-1.5 hover:bg-slate-50 dark:hover:bg-slate-800">
                  <KindIcon kind={source.kind} className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium">{source.title}</span>
                    <span className="block truncate text-[11px] text-slate-400">{hostOf(source.url)}</span>
                  </span>
                  <Button size="sm" variant="secondary" disabled={busy === source.id} onClick={() => void add(source.id)}>
                    {busy === source.id ? <Icon name="spinner" className="h-3 w-3" /> : <Icon name="plus" className="h-3 w-3" />}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

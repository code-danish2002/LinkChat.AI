"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { Collection } from "@/lib/api/types";
import { formatRelativeTime } from "@/lib/format";

export function CollectionList({
  collections,
  loading,
  error,
  onCreate,
  onDelete,
}: {
  collections: Collection[];
  loading: boolean;
  error: string | null;
  onCreate: (title: string) => Promise<unknown>;
  onDelete: (id: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    if (!title.trim() || busy) return;
    setBusy(true);
    try {
      await onCreate(title.trim());
      setTitle("");
      setCreating(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {loading ? (
        <div className="h-16 animate-pulse rounded-xl bg-slate-200/70 dark:bg-slate-800/70" />
      ) : (
        <ul className="space-y-2">
          {collections.map((collection) => (
            <li
              key={collection.id}
              className="group relative rounded-xl bg-white ring-1 ring-slate-200 transition hover:ring-indigo-300 dark:bg-slate-900 dark:ring-slate-800"
            >
              <Link href={`/c/${collection.id}`} className="flex items-center gap-3 p-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                  <Icon name="layers" className="h-4 w-4 text-indigo-500" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{collection.title}</span>
                  <span className="block text-xs text-slate-500 dark:text-slate-400">
                    {collection.source_count} source{collection.source_count === 1 ? "" : "s"}
                    {collection.ready_count < collection.source_count && ` · ${collection.ready_count} ready`}
                    {" · "}
                    {formatRelativeTime(collection.updated_at)}
                  </span>
                </span>
              </Link>
              <button
                type="button"
                onClick={() => onDelete(collection.id)}
                className="absolute -right-2 -top-2 hidden rounded-full bg-white p-1.5 text-slate-500 shadow ring-1 ring-slate-200 hover:text-rose-600 group-hover:block dark:bg-slate-800 dark:ring-slate-700"
                title="Delete collection"
              >
                <Icon name="trash" className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {creating ? (
        <div className="flex gap-2">
          <input
            autoFocus
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void create();
              if (event.key === "Escape") setCreating(false);
            }}
            placeholder="Collection name"
            className="h-9 min-w-0 flex-1 rounded-lg bg-white px-3 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500 dark:bg-slate-900 dark:ring-slate-700"
          />
          <Button size="sm" onClick={() => void create()} disabled={!title.trim() || busy}>
            {busy ? <Icon name="spinner" className="h-3 w-3" /> : <Icon name="check" className="h-3 w-3" />}
          </Button>
        </div>
      ) : (
        <Button variant="secondary" size="sm" className="w-full" onClick={() => setCreating(true)}>
          <Icon name="plus" className="h-3.5 w-3.5" /> New collection
        </Button>
      )}
      {!loading && collections.length === 0 && !creating && (
        <p className="text-xs text-slate-400">Group sources to ask one question across all of them.</p>
      )}
    </div>
  );
}

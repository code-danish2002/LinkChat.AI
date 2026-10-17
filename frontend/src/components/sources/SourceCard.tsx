"use client";

import Link from "next/link";

import { KindIcon } from "@/components/sources/KindIcon";
import { StatusBadge } from "@/components/sources/StatusBadge";
import { Icon } from "@/components/ui/Icon";
import type { Source } from "@/lib/api/types";
import { formatRelativeTime, hostOf } from "@/lib/format";

export function SourceCard({ source, onDelete }: { source: Source; onDelete: (id: string) => void }) {
  const clickable = source.status !== "failed";
  const inner = (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
        <KindIcon kind={source.kind} className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-slate-900 dark:text-slate-100">{source.title}</p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">
          {hostOf(source.url)} · {formatRelativeTime(source.created_at)}
          {source.status === "ready" && ` · ${source.chunk_count} chunks`}
        </p>
        {source.status === "failed" && source.error && (
          <p className="mt-1 line-clamp-2 text-xs text-rose-600 dark:text-rose-400">{source.error}</p>
        )}
      </div>
      <StatusBadge status={source.status} />
    </div>
  );

  return (
    <div className="group relative rounded-xl bg-white p-4 ring-1 ring-slate-200 transition hover:ring-indigo-300 dark:bg-slate-900 dark:ring-slate-800 dark:hover:ring-indigo-700">
      {clickable ? <Link href={`/s/${source.id}`} className="block focus:outline-none">{inner}</Link> : inner}
      <div className="absolute -right-2 -top-2 hidden gap-1 group-hover:flex">
        {source.status === "failed" && (
          <Link
            href={`/s/${source.id}`}
            className="rounded-full bg-white p-1.5 text-slate-500 shadow ring-1 ring-slate-200 hover:text-indigo-600 dark:bg-slate-800 dark:ring-slate-700"
            title="Open and retry"
          >
            <Icon name="refresh" className="h-3.5 w-3.5" />
          </Link>
        )}
        <button
          type="button"
          onClick={() => onDelete(source.id)}
          className="rounded-full bg-white p-1.5 text-slate-500 shadow ring-1 ring-slate-200 hover:text-rose-600 dark:bg-slate-800 dark:ring-slate-700"
          title="Delete source"
        >
          <Icon name="trash" className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

"use client";

import type { ReactNode } from "react";

import { KindIcon } from "@/components/sources/KindIcon";
import { StatusBadge } from "@/components/sources/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { Source } from "@/lib/api/types";
import { formatDuration, formatNumber, hostOf, isUpload, kindLabel } from "@/lib/format";

function Row({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex justify-between gap-3 text-xs">
      <dt className="shrink-0 text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="truncate text-right text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}

export function SourcePanel({
  source,
  onReingest,
  onNewChat,
}: {
  source: Source;
  onReingest: () => void;
  onNewChat: () => void;
}) {
  const meta = source.metadata as Record<string, string | number | boolean | undefined>;
  const thumbnail = typeof meta.thumbnail_url === "string" ? meta.thumbnail_url : null;
  const uploaded = isUpload(source.url);

  return (
    <aside className="flex flex-col gap-4">
      <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
        {thumbnail && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumbnail} alt="" className="aspect-video w-full object-cover" />
        )}
        <div className="space-y-3 p-4">
          <div className="flex items-start gap-2">
            <KindIcon kind={source.kind} className="mt-1 h-4 w-4 shrink-0" />
            <h1 className="text-base font-semibold leading-snug">{source.title}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={source.status} />
            {uploaded ? (
              <span className="inline-flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
                <Icon name="upload" className="h-3 w-3" /> uploaded file
              </span>
            ) : (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline dark:text-indigo-400"
              >
                {hostOf(source.url)} <Icon name="external" className="h-3 w-3" />
              </a>
            )}
          </div>
          {source.status === "failed" && source.error && (
            <p className="rounded-lg bg-rose-50 p-2 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
              {source.error}
            </p>
          )}
          <dl className="space-y-1.5 border-t border-slate-100 pt-3 dark:border-slate-800">
            <Row label="Type" value={kindLabel(source.kind)} />
            <Row label="Chunks" value={source.status === "ready" ? formatNumber(source.chunk_count) : undefined} />
            <Row label="Characters" value={typeof meta.characters === "number" ? formatNumber(meta.characters) : undefined} />
            <Row label="Author" value={typeof meta.author === "string" ? meta.author : undefined} />
            <Row label="Site" value={typeof meta.site_name === "string" ? meta.site_name : undefined} />
            <Row label="Pages" value={typeof meta.pages === "number" ? meta.pages : undefined} />
            <Row label="Slides" value={typeof meta.slides === "number" ? meta.slides : undefined} />
            <Row label="Sheets" value={typeof meta.sheets === "number" ? meta.sheets : undefined} />
            <Row label="Rows" value={typeof meta.rows === "number" ? formatNumber(meta.rows) : undefined} />
            <Row label="Chapters" value={typeof meta.chapters === "number" ? meta.chapters : undefined} />
            <Row label="Duration" value={typeof meta.duration_seconds === "number" ? formatDuration(meta.duration_seconds) : undefined} />
            <Row
              label="Captions"
              value={
                typeof meta.language === "string"
                  ? `${meta.language}${meta.is_generated ? " (auto)" : ""}${meta.translated ? " · translated" : ""}`
                  : undefined
              }
            />
            <Row
              label="Transcribed by"
              value={typeof meta.transcription_model === "string" ? meta.transcription_model.split("/").pop() : undefined}
            />
            <Row label="Rendered" value={meta.rendered ? "headless browser" : undefined} />
            <Row
              label="Embeddings"
              value={typeof meta.embedding_model === "string" ? meta.embedding_model.split("/").pop() : undefined}
            />
          </dl>
          <div className="flex gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={onNewChat} disabled={source.status !== "ready"}>
              <Icon name="plus" className="h-3.5 w-3.5" /> New chat
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={onReingest}
              disabled={source.status === "processing" || source.status === "pending"}
            >
              <Icon name="refresh" className="h-3.5 w-3.5" /> Re-read
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}

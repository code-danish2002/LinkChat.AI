import { Icon } from "@/components/ui/Icon";
import type { Source } from "@/lib/api/types";

export function PendingNotice({ source }: { source: Source }) {
  if (source.status === "failed") {
    return (
      <div className="max-w-md space-y-2">
        <Icon name="alert" className="mx-auto h-8 w-8 text-rose-500" />
        <p className="font-medium">This source could not be read</p>
        <p className="text-sm text-slate-500 dark:text-slate-400">{source.error ?? "Unknown error."}</p>
        <p className="text-xs text-slate-400">Use “Re-read” in the panel to try again.</p>
      </div>
    );
  }
  const transcribing = source.kind === "audio";
  return (
    <div className="space-y-2">
      <Icon name="spinner" className="mx-auto h-8 w-8 text-indigo-500" />
      <p className="font-medium">
        {source.status === "pending" ? "Queued…" : transcribing ? "Transcribing the audio…" : "Reading and indexing…"}
      </p>
      <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
        {transcribing
          ? "Speech to text runs first, then the transcript is split and indexed. Long recordings take a while."
          : "Fetching, splitting into chunks and computing embeddings. Usually a few seconds."}
      </p>
    </div>
  );
}

export function CollectionPendingNotice({ ready, total }: { ready: number; total: number }) {
  if (total === 0) {
    return (
      <div className="max-w-md space-y-2">
        <Icon name="layers" className="mx-auto h-8 w-8 text-slate-400" />
        <p className="font-medium">This collection is empty</p>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Add sources from the panel, then chat across all of them at once.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <Icon name="spinner" className="mx-auto h-8 w-8 text-indigo-500" />
      <p className="font-medium">Preparing sources…</p>
      <p className="text-sm text-slate-500 dark:text-slate-400">
        {ready} of {total} ready. Chat opens as soon as the first one finishes.
      </p>
    </div>
  );
}

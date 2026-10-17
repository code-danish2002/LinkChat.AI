"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { api, ApiError } from "@/lib/api/client";
import type { Capabilities, Source } from "@/lib/api/types";

export function SourceForm({
  onCreated,
  capabilities,
  autoFocus = true,
}: {
  onCreated: (source: Source) => void;
  capabilities: Capabilities | null;
  autoFocus?: boolean;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function run(action: () => Promise<Source>) {
    setBusy(true);
    setError(null);
    try {
      onCreated(await action());
      setUrl("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add that source.");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!url.trim() || busy) return;
    void run(() => api.sources.create(url.trim()));
  }

  function pick(files: FileList | null) {
    const file = files?.[0];
    if (!file || busy) return;
    void run(() => api.sources.upload(file));
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    pick(event.dataTransfer.files);
  }

  const accept = capabilities?.upload_extensions.join(",");

  return (
    <form
      onSubmit={submit}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className="w-full"
    >
      <div
        className={`flex items-center gap-2 rounded-2xl bg-white p-2 shadow-lg shadow-slate-200/60 ring-1 transition focus-within:ring-2 focus-within:ring-indigo-500 dark:bg-slate-900 dark:shadow-none ${
          dragging ? "ring-2 ring-indigo-500" : "ring-slate-200 dark:ring-slate-700"
        }`}
      >
        <Icon name="link" className="ml-2 h-5 w-5 shrink-0 text-slate-400" />
        <input
          type="url"
          inputMode="url"
          autoFocus={autoFocus}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={dragging ? "Drop the file to upload it" : "Paste a link, or drop a file here"}
          className="h-11 min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-slate-400"
          aria-label="URL to chat with"
        />
        <input
          ref={fileInput}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => pick(e.target.files)}
        />
        <Button
          type="button"
          variant="ghost"
          onClick={() => fileInput.current?.click()}
          disabled={busy}
          title="Upload a document or audio file"
          className="shrink-0"
        >
          <Icon name="upload" />
          <span className="sr-only">Upload a file</span>
        </Button>
        <Button type="submit" disabled={busy || !url.trim()} className="shrink-0">
          {busy ? <Icon name="spinner" /> : <Icon name="sparkles" />}
          <span className="hidden sm:inline">Start chatting</span>
        </Button>
      </div>

      {error ? (
        <p role="alert" className="mt-2 flex items-center gap-1.5 text-sm text-rose-600 dark:text-rose-400">
          <Icon name="alert" className="h-4 w-4 shrink-0" /> {error}
        </p>
      ) : (
        capabilities && (
          <p className="mt-2 text-center text-xs text-slate-400">
            Links, PDFs, Word, slides, spreadsheets, EPUB and plain text
            {capabilities.audio_transcription ? ", plus audio that gets transcribed" : ""}. Up to{" "}
            {capabilities.max_upload_mb} MB per file.
          </p>
        )
      )}
    </form>
  );
}

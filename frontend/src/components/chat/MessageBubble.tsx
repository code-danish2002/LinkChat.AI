"use client";

import { useState } from "react";

import { Citations } from "@/components/chat/Citations";
import { Markdown } from "@/components/chat/Markdown";
import { modeIcon } from "@/components/chat/ModeBar";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { ChatMessage } from "@/hooks/useChat";
import type { Mode } from "@/lib/api/types";
import { modelLabel } from "@/lib/format";

export function MessageBubble({
  message,
  modes,
  onRetry,
}: {
  message: ChatMessage;
  modes: Mode[];
  onRetry?: () => void;
}) {
  const [highlight, setHighlight] = useState<number | null>(null);
  const mode = modes.find((m) => m.id === message.mode);

  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1">
        {mode && mode.id !== "qa" && (
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-indigo-600 dark:text-indigo-400">
            <Icon name={modeIcon(mode.id)} className="h-3 w-3" /> {mode.label}
          </span>
        )}
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-[15px] text-white shadow-sm">
          {message.content}
        </div>
      </div>
    );
  }

  const waiting = message.streaming && !message.content;
  return (
    <div className="flex gap-3">
      <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900">
        <Icon name="sparkles" className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 max-w-[90%] flex-1 rounded-2xl rounded-tl-md bg-white px-4 py-3 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
        {waiting ? (
          <div className="flex items-center gap-1 py-1" aria-label="Working">
            <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
            <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
            <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
            <span className="ml-2 text-xs text-slate-500">{message.progress ?? "Reading the source…"}</span>
          </div>
        ) : (
          <Markdown
            content={message.content}
            onCite={(index) => {
              setHighlight(null);
              requestAnimationFrame(() => setHighlight(index));
            }}
          />
        )}
        {message.progress && message.content && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
            <Icon name="spinner" className="h-3 w-3" /> {message.progress}
          </p>
        )}
        {message.error && (
          <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-rose-50 p-2 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
            <Icon name="alert" className="h-4 w-4 shrink-0" />
            <span className="flex-1">{message.error}</span>
            {onRetry && (
              <Button variant="secondary" size="sm" onClick={onRetry}>
                <Icon name="refresh" className="h-3 w-3" /> Retry
              </Button>
            )}
          </div>
        )}
        {!waiting && <Citations citations={message.citations} highlight={highlight} />}
        {message.model && !message.streaming && (
          <p className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
            answered by {modelLabel(message.model)}
          </p>
        )}
      </div>
    </div>
  );
}

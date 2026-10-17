"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "@/components/chat/Composer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ModeBar, modeIcon } from "@/components/chat/ModeBar";
import { Icon } from "@/components/ui/Icon";
import { useChat } from "@/hooks/useChat";
import type { ChatModeId, Mode, SourceKind } from "@/lib/api/types";

const QA: Mode = {
  id: "qa",
  label: "Ask",
  description: "Answer a question from the most relevant passages.",
  scope: "retrieval",
  needs_input: true,
  input_label: "Question",
  input_placeholder: "Ask anything about this source…",
  default_prompt: "",
};

const QUICK_BY_KIND: Partial<Record<SourceKind, ChatModeId[]>> = {
  youtube: ["summary", "outline", "key_points"],
  audio: ["summary", "outline", "action_items"],
  pdf: ["summary", "key_points", "flashcards"],
  epub: ["summary", "outline", "flashcards"],
  pptx: ["summary", "key_points", "action_items"],
  xlsx: ["extract", "key_points", "summary"],
  web: ["summary", "key_points", "extract"],
};

export function ChatPanel({
  title,
  kind,
  ready,
  pending,
  conversationId,
  modes,
}: {
  title: string;
  kind: SourceKind;
  ready: boolean;
  pending: React.ReactNode;
  conversationId: string | null;
  modes: Mode[];
}) {
  const chat = useChat(conversationId);
  const bottom = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<ChatModeId>("qa");

  const catalogue = useMemo(() => (modes.length ? modes : [QA]), [modes]);
  const active = useMemo(
    () => catalogue.find((mode) => mode.id === activeId) ?? catalogue[0],
    [catalogue, activeId],
  );
  const live = ready && conversationId !== null;

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.messages]);

  function selectMode(mode: Mode) {
    setActiveId(mode.id);
  }

  function runMode(mode: Mode) {
    setActiveId(mode.id);
    if (!mode.needs_input) void chat.send("", mode.id, mode.default_prompt);
  }

  const quick = (QUICK_BY_KIND[kind] ?? ["summary", "key_points", "extract"])
    .map((id) => catalogue.find((mode) => mode.id === id))
    .filter((mode): mode is Mode => Boolean(mode));

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="scrollbar-thin flex-1 space-y-4 overflow-y-auto px-1 py-4">
        {chat.loading && live && (
          <p className="flex items-center gap-2 text-sm text-slate-500">
            <Icon name="spinner" /> Loading conversation…
          </p>
        )}
        {chat.error && <p className="text-sm text-rose-600 dark:text-rose-400">{chat.error}</p>}

        {!chat.loading && chat.messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            {live ? (
              <>
                <div className="space-y-1">
                  <p className="font-medium">Ask anything about “{title}”</p>
                  <p className="text-sm text-slate-500 dark:text-slate-400">
                    Answers are grounded in the source and cite the exact excerpts used.
                  </p>
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  {quick.map((mode) => (
                    <button
                      key={mode.id}
                      type="button"
                      onClick={() => runMode(mode)}
                      className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-200 hover:ring-indigo-400 dark:bg-slate-900 dark:text-slate-200 dark:ring-slate-700"
                    >
                      <Icon name={modeIcon(mode.id)} className="h-3.5 w-3.5 text-indigo-500" />
                      {mode.label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-slate-400">One click, no typing needed.</p>
              </>
            ) : (
              pending
            )}
          </div>
        )}

        {chat.messages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            modes={catalogue}
            onRetry={index === chat.messages.length - 1 && message.error ? chat.retryLast : undefined}
          />
        ))}
        <div ref={bottom} />
      </div>

      <div className="sticky bottom-0 space-y-2 bg-gradient-to-t from-slate-50 via-slate-50 pb-4 pt-2 dark:from-slate-950 dark:via-slate-950">
        <ModeBar modes={catalogue} active={active.id} disabled={!live || chat.streaming} onSelect={selectMode} />
        <Composer
          mode={active}
          disabled={!live}
          streaming={chat.streaming}
          onSend={(text) => void chat.send(text, active.id, active.default_prompt)}
          onStop={chat.stop}
        />
        <p className="text-center text-[11px] text-slate-400">
          {active.needs_input
            ? "Enter to send · Shift+Enter for a new line"
            : `${active.description} Press Run, or add an instruction first.`}
        </p>
      </div>
    </section>
  );
}

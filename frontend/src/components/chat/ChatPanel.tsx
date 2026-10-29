"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "@/components/chat/Composer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ModeBar, modeIcon } from "@/components/chat/ModeBar";
import { VoiceSession } from "@/components/chat/VoiceSession";
import { Icon } from "@/components/ui/Icon";
import { useChat, type ChatMessage } from "@/hooks/useChat";
import type { ChatModeId, ChatStreamEvent, Mode, SourceKind } from "@/lib/api/types";
import { modelLabel } from "@/lib/format";
import { Speaker, languageTag, supportsSpeechInput, supportsSpeechOutput } from "@/lib/speech";

const READ_ALOUD_KEY = "linkchat.readAloud";

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
  voice = false,
}: {
  title: string;
  kind: SourceKind;
  ready: boolean;
  pending: React.ReactNode;
  conversationId: string | null;
  modes: Mode[];
  /** The server can transcribe spoken questions. */
  voice?: boolean;
}) {
  const chat = useChat(conversationId);
  const bottom = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<ChatModeId>("qa");
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [readAloud, setReadAloud] = useState(false);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const speakerRef = useRef<Speaker | null>(null);
  const speechOut = supportsSpeechOutput();
  const voiceAvailable = voice && supportsSpeechInput();

  // One speaker per panel for typed answers and the per-message buttons; the
  // voice session owns its own so the two never talk over each other.
  const speaker = useCallback(() => {
    if (!speakerRef.current) {
      speakerRef.current = new Speaker();
      speakerRef.current.onChange((speaking) => {
        if (!speaking) setSpeakingId(null);
      });
    }
    return speakerRef.current;
  }, []);

  useEffect(() => {
    try {
      setReadAloud(localStorage.getItem(READ_ALOUD_KEY) === "1");
    } catch {
      /* no storage */
    }
    return () => speakerRef.current?.stop();
  }, []);

  function toggleReadAloud() {
    const next = !readAloud;
    setReadAloud(next);
    if (!next) speaker().stop();
    try {
      localStorage.setItem(READ_ALOUD_KEY, next ? "1" : "0");
    } catch {
      /* no storage */
    }
  }

  /** Typed questions: when read-aloud is on, speak the answer as it streams. */
  const sendTyped = useCallback(
    (text: string, mode: Mode) => {
      if (!readAloud || !speechOut) return void chat.send(text, mode.id, mode.default_prompt);
      const voiceOut = speaker();
      voiceOut.stop();
      voiceOut.setLanguage(languageTag(null, text));
      setSpeakingId("live");
      const onEvent = (event: ChatStreamEvent) => {
        if (event.type === "token") voiceOut.feed(event.data);
        else if (event.type === "done") {
          voiceOut.setLanguage(languageTag(event.data.language, text));
          voiceOut.flush();
        } else if (event.type === "error") voiceOut.stop();
      };
      void chat.send(text, mode.id, mode.default_prompt, { spoken: true, onEvent });
    },
    [chat, readAloud, speechOut, speaker],
  );

  function speakMessage(message: ChatMessage) {
    const voiceOut = speaker();
    if (speakingId === message.id) {
      voiceOut.stop();
      return;
    }
    const question = [...chat.messages].reverse().find((m) => m.role === "user" && m.created_at <= message.created_at);
    voiceOut.speakAll(message.content, languageTag(null, question?.content ?? message.content));
    setSpeakingId(message.id);
  }

  function stopAll() {
    speaker().stop();
    chat.stop();
  }

  const catalogue = useMemo(() => (modes.length ? modes : [QA]), [modes]);
  const active = useMemo(
    () => catalogue.find((mode) => mode.id === activeId) ?? catalogue[0],
    [catalogue, activeId],
  );
  const live = ready && conversationId !== null;
  // The model this conversation is on, kept visible between turns rather than
  // only appearing under a finished answer.
  const currentModel = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
      const model = chat.messages[i].model;
      if (model) return model;
    }
    return null;
  }, [chat.messages]);

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
            onSpeak={speechOut && message.role === "assistant" ? speakMessage : undefined}
            speaking={speakingId === message.id}
          />
        ))}
        <div ref={bottom} />
      </div>

      <div className="sticky bottom-0 space-y-2 bg-gradient-to-t from-slate-50 via-slate-50 pb-4 pt-2 dark:from-slate-950 dark:via-slate-950">
        {voiceOpen && live ? (
          <div className="flex min-h-[22rem] flex-col">
            <VoiceSession
              title={title}
              mode={active}
              sendVoice={(audio, onEvent) => chat.sendVoice(audio, active.id, { onEvent })}
              stopAnswer={chat.stop}
              onClose={() => {
                chat.stop();
                setVoiceOpen(false);
              }}
            />
          </div>
        ) : (
          <>
            <ModeBar modes={catalogue} active={active.id} disabled={!live || chat.streaming} onSelect={selectMode} />
            <Composer
              mode={active}
              disabled={!live}
              streaming={chat.streaming}
              onSend={(text) => sendTyped(text, active)}
              onStop={stopAll}
              voiceAvailable={voiceAvailable}
              onVoice={() => {
                speaker().stop();
                setVoiceOpen(true);
              }}
              readAloud={readAloud}
              onToggleReadAloud={speechOut ? toggleReadAloud : undefined}
            />
          </>
        )}
        <p className="flex items-center justify-center gap-2 text-center text-[11px] text-slate-400">
          {currentModel && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300"
              title={`This conversation is answered by ${currentModel}. The pool picks another if it fails.`}
            >
              <Icon name="sparkles" className="h-3 w-3 text-indigo-500" />
              {modelLabel(currentModel)}
            </span>
          )}
          <span>
            {voiceOpen
              ? "Speak naturally. Pause, and the answer is read back to you."
              : active.needs_input
                ? `Enter to send · Shift+Enter for a new line${voiceAvailable ? " · mic to talk" : ""}`
                : `${active.description} Press Run, or add an instruction first.`}
          </span>
        </p>
      </div>
    </section>
  );
}

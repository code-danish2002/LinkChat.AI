"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { Mode } from "@/lib/api/types";

export function Composer({
  mode,
  disabled,
  streaming,
  onSend,
  onStop,
  voiceAvailable = false,
  onVoice,
  readAloud = false,
  onToggleReadAloud,
}: {
  mode: Mode;
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** The server accepts spoken questions and this browser can record. */
  voiceAvailable?: boolean;
  onVoice?: () => void;
  /** Answers are read aloud as they arrive. */
  readAloud?: boolean;
  onToggleReadAloud?: () => void;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (!disabled && !streaming) ref.current?.focus();
  }, [disabled, streaming, mode.id]);

  const ready = !disabled && !streaming;
  const canSend = ready && (!mode.needs_input || value.trim().length > 0);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  const placeholder = disabled
    ? "Waiting for the source to be ready…"
    : (mode.input_placeholder ?? (mode.needs_input ? "Type your input…" : "Add an optional instruction, or just press Run"));

  const iconButton =
    "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <form
      onSubmit={submit}
      className="flex items-end gap-1.5 rounded-2xl bg-white p-2 ring-1 ring-slate-200 focus-within:ring-2 focus-within:ring-indigo-500 dark:bg-slate-900 dark:ring-slate-700"
    >
      <textarea
        ref={ref}
        rows={1}
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
        aria-label={mode.input_label ?? "Message"}
      />
      {onToggleReadAloud && (
        <button
          type="button"
          onClick={onToggleReadAloud}
          aria-pressed={readAloud}
          title={readAloud ? "Answers are read aloud. Click to stop." : "Read answers aloud"}
          className={`${iconButton} ${
            readAloud
              ? "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/40 dark:text-indigo-300"
              : "text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
          }`}
        >
          <Icon name={readAloud ? "speaker" : "speakerOff"} className="h-4.5 w-4.5" />
        </button>
      )}
      {voiceAvailable && onVoice && !streaming && (
        <button
          type="button"
          onClick={onVoice}
          disabled={disabled}
          title="Talk instead of typing"
          aria-label="Start a voice conversation"
          className={`${iconButton} text-slate-500 hover:bg-slate-100 hover:text-indigo-600 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-indigo-300`}
        >
          <Icon name="mic" className="h-4.5 w-4.5" />
        </button>
      )}
      {streaming ? (
        <Button type="button" variant="secondary" onClick={onStop} title="Stop generating">
          <Icon name="stop" /> Stop
        </Button>
      ) : (
        <Button type="submit" disabled={!canSend} title={mode.needs_input ? "Send (Enter)" : `Run ${mode.label}`}>
          <Icon name="send" />
          {!mode.needs_input && <span className="hidden sm:inline">Run</span>}
        </Button>
      )}
    </form>
  );
}

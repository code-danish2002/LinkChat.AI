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
}: {
  mode: Mode;
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
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

  return (
    <form
      onSubmit={submit}
      className="flex items-end gap-2 rounded-2xl bg-white p-2 ring-1 ring-slate-200 focus-within:ring-2 focus-within:ring-indigo-500 dark:bg-slate-900 dark:ring-slate-700"
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

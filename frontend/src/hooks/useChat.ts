"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { ChatModeId, ChatStreamEvent, Citation, Message } from "@/lib/api/types";

export interface ChatMessage extends Message {
  /** True while the assistant answer is still streaming. */
  streaming?: boolean;
  /** Latest status line from a multi-pass run. */
  progress?: string | null;
  /** Error text when the answer could not be produced. */
  error?: string | null;
  /** The question arrived by voice. */
  spoken?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  loading: boolean;
  streaming: boolean;
  error: string | null;
}

export interface SendOptions {
  /** Ask for an answer written to be read aloud. */
  spoken?: boolean;
  /** Observe every stream event, for example to speak tokens as they arrive. */
  onEvent?: (event: ChatStreamEvent) => void;
}

let tempCounter = 0;
const tempId = (prefix: string) => `${prefix}_tmp_${Date.now()}_${tempCounter++}`;

export function useChat(conversationId: string | null) {
  const [state, setState] = useState<ChatState>({ messages: [], loading: true, streaming: false, error: null });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, messages: [], error: null }));
    api.conversations
      .messages(conversationId)
      .then((messages) => !cancelled && setState({ messages, loading: false, streaming: false, error: null }))
      .catch((err) => !cancelled && setState({ messages: [], loading: false, streaming: false, error: err.message }));
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [conversationId]);

  const patch = useCallback(
    (id: string, update: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) => {
      setState((s) => ({
        ...s,
        messages: s.messages.map((m) => (m.id === id ? { ...m, ...(typeof update === "function" ? update(m) : update) } : m)),
      }));
    },
    [],
  );

  /** Shared by typed and spoken turns: optimistic bubbles, then the event stream. */
  const run = useCallback(
    async (
      shown: string,
      mode: ChatModeId,
      spoken: boolean,
      start: (signal: AbortSignal) => AsyncGenerator<ChatStreamEvent>,
      onEvent?: (event: ChatStreamEvent) => void,
    ) => {
      if (!conversationId || abortRef.current) return;

      const userId = tempId("usr");
      const assistantId = tempId("ast");
      const now = new Date().toISOString();
      const base = { conversation_id: conversationId, model: null, mode, citations: [] as Citation[], created_at: now };

      setState((s) => ({
        ...s,
        streaming: true,
        error: null,
        messages: [
          ...s.messages,
          { ...base, id: userId, role: "user" as const, content: shown, spoken },
          { ...base, id: assistantId, role: "assistant" as const, content: "", streaming: true },
        ],
      }));

      const controller = new AbortController();
      abortRef.current = controller;
      let failed = false;

      try {
        for await (const event of start(controller.signal)) {
          // Cancelling the stream resolves pending reads rather than throwing,
          // so without this check already-buffered tokens keep being appended
          // for a moment after Stop is pressed.
          if (controller.signal.aborted) break;
          onEvent?.(event);
          switch (event.type) {
            case "transcript":
              patch(userId, { content: event.data.text });
              break;
            case "citations":
              patch(assistantId, { citations: event.data });
              break;
            case "progress":
              patch(assistantId, { progress: event.data });
              break;
            case "model":
              patch(assistantId, { model: event.data });
              break;
            case "token":
              patch(assistantId, (m) => ({ content: m.content + event.data, progress: null }));
              break;
            case "done":
              setState((s) => ({
                ...s,
                messages: s.messages.map((m) =>
                  m.id === userId
                    ? { ...m, id: event.data.user_message_id }
                    : m.id === assistantId
                      ? { ...m, id: event.data.assistant_message_id, model: event.data.model, streaming: false, progress: null }
                      : m,
                ),
              }));
              break;
            case "error":
              failed = true;
              patch(assistantId, { streaming: false, progress: null, error: event.data });
              break;
          }
        }
      } catch (err) {
        failed = true;
        const message =
          err instanceof ApiError
            ? err.message
            : controller.signal.aborted
              ? "Stopped."
              : "The answer could not be streamed.";
        patch(assistantId, { streaming: false, progress: null, error: message });
      } finally {
        const stopped = controller.signal.aborted;
        abortRef.current = null;
        setState((s) => ({
          ...s,
          streaming: false,
          messages: s.messages.map((m) => {
            if (m.id !== assistantId || !m.streaming) return m;
            // Say why the answer is cut short. Without this a stopped answer
            // looks like the model simply trailed off mid-sentence.
            const reason = stopped ? "Stopped. The answer is incomplete." : failed ? "Interrupted." : null;
            return { ...m, streaming: false, progress: null, error: m.error ?? reason };
          }),
        }));
      }
    },
    [conversationId, patch],
  );

  const send = useCallback(
    async (content: string, mode: ChatModeId = "qa", displayPrompt?: string, options: SendOptions = {}) => {
      const text = content.trim();
      if (!conversationId) return;
      const shown = text || displayPrompt || "Run";
      await run(
        shown,
        mode,
        false,
        (signal) => api.conversations.send(conversationId, text, mode, signal, options.spoken ?? false),
        options.onEvent,
      );
    },
    [conversationId, run],
  );

  /** A spoken question. The bubble shows a placeholder until the transcript arrives. */
  const sendVoice = useCallback(
    async (audio: Blob, mode: ChatModeId = "qa", options: SendOptions = {}) => {
      if (!conversationId) return;
      await run("…", mode, true, (signal) => api.conversations.voice(conversationId, audio, mode, signal), options.onEvent);
    },
    [conversationId, run],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const retryLast = useCallback(async () => {
    const lastUser = [...state.messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    const mode = lastUser.mode;
    setState((s) => {
      const index = s.messages.findIndex((m) => m.id === lastUser.id);
      return { ...s, messages: index === -1 ? s.messages : s.messages.slice(0, index) };
    });
    await send(mode === "qa" ? lastUser.content : "", mode, lastUser.content, { spoken: lastUser.spoken });
  }, [state.messages, send]);

  return { ...state, send, sendVoice, stop, retryLast };
}

"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import type { Conversation } from "@/lib/api/types";

export type ConversationTarget = { source_id: string } | { collection_id: string };

/** Resolves the thread for a target: reuses the most recent one or creates it. */
export function useConversation(target: ConversationTarget, enabled: boolean) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = "source_id" in target ? `s:${target.source_id}` : `c:${target.collection_id}`;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    (async () => {
      try {
        const existing =
          "source_id" in target
            ? await api.sources.conversations(target.source_id)
            : await api.collections.conversations(target.collection_id);
        const chosen = existing[0] ?? (await api.conversations.create(target));
        if (!cancelled) setConversation(chosen);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to open the conversation.");
      }
    })();
    return () => {
      cancelled = true;
    };
    // `key` identifies the target; the object literal is recreated every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);

  const startNew = useCallback(async () => {
    const created = await api.conversations.create(target);
    setConversation(created);
    return created;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { conversation, error, startNew };
}

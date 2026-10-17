"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { Source } from "@/lib/api/types";

/** Recent sources with light polling while any of them is still ingesting. */
export function useSources() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.sources.list();
      setSources(list);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load sources.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const busy = sources.some((s) => s.status === "pending" || s.status === "processing");
    if (!busy) return;
    timer.current = setTimeout(() => void refresh(), 2000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [sources, refresh]);

  const remove = useCallback(async (id: string) => {
    await api.sources.remove(id);
    setSources((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const add = useCallback((source: Source) => {
    setSources((prev) => [source, ...prev.filter((s) => s.id !== source.id)]);
  }, []);

  return { sources, loading, error, refresh, remove, add };
}

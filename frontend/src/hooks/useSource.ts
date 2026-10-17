"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { Source } from "@/lib/api/types";

const POLL_MS = 1500;

/** One source, polled until ingestion finishes. */
export function useSource(id: string) {
  const [source, setSource] = useState<Source | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.sources.get(id);
      setSource(next);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setNotFound(true);
      else setError(err instanceof Error ? err.message : "Failed to load source.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!source || (source.status !== "pending" && source.status !== "processing")) return;
    const timer = setTimeout(() => void load(), POLL_MS);
    return () => clearTimeout(timer);
  }, [source, load]);

  const reingest = useCallback(async () => {
    setSource(await api.sources.reingest(id));
  }, [id]);

  return { source, error, notFound, reingest, reload: load };
}

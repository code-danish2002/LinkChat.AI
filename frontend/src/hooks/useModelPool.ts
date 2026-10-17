"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import type { ModelPool } from "@/lib/api/types";

export function useModelPool(intervalMs = 15000) {
  const [pool, setPool] = useState<ModelPool | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setPool(await api.models());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Model pool unavailable.");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs]);

  return { pool, error, refresh };
}

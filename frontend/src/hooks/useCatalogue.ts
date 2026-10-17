"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import type { Capabilities, Mode } from "@/lib/api/types";

interface Catalogue {
  modes: Mode[];
  capabilities: Capabilities | null;
  loading: boolean;
  error: string | null;
}

/** Modes and server capabilities. Both are static per deployment, so fetch once. */
export function useCatalogue(): Catalogue {
  const [state, setState] = useState<Catalogue>({ modes: [], capabilities: null, loading: true, error: null });

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.modes(), api.health()])
      .then(([modes, health]) => {
        if (!cancelled) setState({ modes, capabilities: health.capabilities, loading: false, error: null });
      })
      .catch((err) => {
        if (!cancelled)
          setState({ modes: [], capabilities: null, loading: false, error: err instanceof Error ? err.message : "Unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

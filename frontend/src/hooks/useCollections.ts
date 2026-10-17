"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { Collection } from "@/lib/api/types";

export function useCollections() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setCollections(await api.collections.list());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load collections.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = useCallback(async (title: string, sourceIds: string[] = []) => {
    const created = await api.collections.create(title, sourceIds);
    setCollections((prev) => [created, ...prev]);
    return created;
  }, []);

  const remove = useCallback(async (id: string) => {
    await api.collections.remove(id);
    setCollections((prev) => prev.filter((c) => c.id !== id));
  }, []);

  return { collections, loading, error, refresh, create, remove };
}

/** One collection plus its member sources, polled while any member is ingesting. */
export function useCollection(id: string) {
  const [collection, setCollection] = useState<Collection | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCollection(await api.collections.get(id));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setNotFound(true);
      else setError(err instanceof Error ? err.message : "Failed to load collection.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!collection || collection.ready_count === collection.source_count) return;
    const timer = setTimeout(() => void load(), 2000);
    return () => clearTimeout(timer);
  }, [collection, load]);

  const addSource = useCallback(
    async (sourceId: string) => setCollection(await api.collections.addSource(id, sourceId)),
    [id],
  );
  const removeSource = useCallback(
    async (sourceId: string) => setCollection(await api.collections.removeSource(id, sourceId)),
    [id],
  );
  const rename = useCallback(
    async (title: string) => setCollection(await api.collections.update(id, { title })),
    [id],
  );

  return { collection, notFound, error, reload: load, addSource, removeSource, rename };
}

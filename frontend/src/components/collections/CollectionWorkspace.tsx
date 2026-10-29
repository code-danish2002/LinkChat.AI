"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ChatPanel } from "@/components/chat/ChatPanel";
import { CollectionPendingNotice } from "@/components/chat/PendingNotice";
import { SourcePicker } from "@/components/collections/SourcePicker";
import { AppShell } from "@/components/layout/AppShell";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { ModelPool } from "@/components/models/ModelPool";
import { KindIcon } from "@/components/sources/KindIcon";
import { StatusBadge } from "@/components/sources/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { useCatalogue } from "@/hooks/useCatalogue";
import { useCollection } from "@/hooks/useCollections";
import { useConversation } from "@/hooks/useConversation";
import { useSources } from "@/hooks/useSources";
import { api } from "@/lib/api/client";

export function CollectionWorkspace({ collectionId }: { collectionId: string }) {
  const router = useRouter();
  const { collection, notFound, error, addSource, removeSource, rename } = useCollection(collectionId);
  const { sources } = useSources();
  const { modes, capabilities } = useCatalogue();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const ready = (collection?.ready_count ?? 0) > 0;
  const { conversation, error: conversationError, startNew } = useConversation(
    { collection_id: collectionId },
    ready,
  );

  useEffect(() => {
    if (collection) document.title = `${collection.title} · LinkChat`;
  }, [collection]);

  const headerRight = (
    <>
      <Link href="/" className="inline-flex items-center gap-1 hover:text-slate-900 dark:hover:text-white">
        <Icon name="plus" className="h-4 w-4" /> New source
      </Link>
      <ThemeToggle />
    </>
  );

  if (notFound) {
    return (
      <AppShell right={headerRight}>
        <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
          <Icon name="alert" className="h-8 w-8 text-slate-400" />
          <p className="font-medium">This collection does not exist anymore.</p>
          <Button variant="secondary" onClick={() => router.push("/")}>
            Back to home
          </Button>
        </div>
      </AppShell>
    );
  }

  if (!collection) {
    return (
      <AppShell right={headerRight}>
        <div className="flex flex-1 items-center justify-center py-24 text-sm text-slate-500">
          {error ? (
            <span className="text-rose-600 dark:text-rose-400">{error}</span>
          ) : (
            <>
              <Icon name="spinner" className="mr-2 h-4 w-4" /> Loading…
            </>
          )}
        </div>
      </AppShell>
    );
  }

  const members = collection.source_ids
    .map((id) => sources.find((source) => source.id === id))
    .filter((source): source is NonNullable<typeof source> => Boolean(source));

  return (
    <AppShell right={headerRight}>
      <div className="grid flex-1 gap-6 py-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
            <div className="flex items-start gap-2">
              <Icon name="layers" className="mt-1 h-4 w-4 shrink-0 text-indigo-500" />
              {editing ? (
                <input
                  autoFocus
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onBlur={() => {
                    if (draft.trim() && draft.trim() !== collection.title) void rename(draft.trim());
                    setEditing(false);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                    if (event.key === "Escape") setEditing(false);
                  }}
                  className="min-w-0 flex-1 rounded bg-slate-50 px-1 text-base font-semibold outline-none ring-1 ring-indigo-400 dark:bg-slate-800"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setDraft(collection.title);
                    setEditing(true);
                  }}
                  className="min-w-0 flex-1 text-left text-base font-semibold leading-snug hover:text-indigo-600 dark:hover:text-indigo-400"
                  title="Rename"
                >
                  {collection.title}
                </button>
              )}
            </div>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              {collection.ready_count} of {collection.source_count} source
              {collection.source_count === 1 ? "" : "s"} ready. Questions search all of them.
            </p>

            <ul className="mt-3 space-y-1 border-t border-slate-100 pt-3 dark:border-slate-800">
              {members.map((source) => (
                <li key={source.id} className="group flex items-center gap-2">
                  <KindIcon kind={source.kind} className="h-3.5 w-3.5 shrink-0" />
                  <Link href={`/s/${source.id}`} className="min-w-0 flex-1 truncate text-xs hover:underline">
                    {source.title}
                  </Link>
                  {source.status !== "ready" && <StatusBadge status={source.status} />}
                  <button
                    type="button"
                    onClick={() => void removeSource(source.id)}
                    className="hidden text-slate-400 hover:text-rose-600 group-hover:block"
                    title="Remove from collection"
                  >
                    <Icon name="close" className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
              {members.length === 0 && (
                <li className="text-xs text-slate-400">No sources yet.</li>
              )}
            </ul>

            <div className="mt-3 flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => void startNew()} disabled={!ready}>
                <Icon name="plus" className="h-3.5 w-3.5" /> New chat
              </Button>
            </div>
          </div>

          <SourcePicker sources={sources} excluded={collection.source_ids} onAdd={addSource} />
          <ModelPool compact />
          <Button
            variant="danger"
            size="sm"
            className="w-full"
            onClick={async () => {
              if (!confirm("Delete this collection? The sources themselves are kept.")) return;
              await api.collections.remove(collection.id);
              router.push("/");
            }}
          >
            <Icon name="trash" className="h-3.5 w-3.5" /> Delete collection
          </Button>
        </div>

        <div className="flex min-h-[70vh] flex-col lg:h-[calc(100vh-7.5rem)]">
          {conversationError && <p className="mb-2 text-sm text-rose-600 dark:text-rose-400">{conversationError}</p>}
          <ChatPanel
            title={collection.title}
            kind="web"
            ready={ready}
            pending={<CollectionPendingNotice ready={collection.ready_count} total={collection.source_count} />}
            conversationId={conversation?.id ?? null}
            modes={modes}
            voice={capabilities?.voice ?? false}
          />
        </div>
      </div>
    </AppShell>
  );
}

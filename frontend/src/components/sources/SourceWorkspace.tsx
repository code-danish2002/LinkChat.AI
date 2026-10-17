"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ChatPanel } from "@/components/chat/ChatPanel";
import { PendingNotice } from "@/components/chat/PendingNotice";
import { AppShell } from "@/components/layout/AppShell";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { ModelPool } from "@/components/models/ModelPool";
import { SourcePanel } from "@/components/sources/SourcePanel";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { useCatalogue } from "@/hooks/useCatalogue";
import { useConversation } from "@/hooks/useConversation";
import { useSource } from "@/hooks/useSource";
import { api } from "@/lib/api/client";

export function SourceWorkspace({ sourceId }: { sourceId: string }) {
  const router = useRouter();
  const { source, error, notFound, reingest } = useSource(sourceId);
  const { modes } = useCatalogue();
  const ready = source?.status === "ready";
  const { conversation, error: conversationError, startNew } = useConversation({ source_id: sourceId }, ready);

  useEffect(() => {
    if (source) document.title = `${source.title} · LinkChat`;
  }, [source]);

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
          <p className="font-medium">This source does not exist anymore.</p>
          <Button variant="secondary" onClick={() => router.push("/")}>
            Back to home
          </Button>
        </div>
      </AppShell>
    );
  }

  if (!source) {
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

  return (
    <AppShell right={headerRight}>
      <div className="grid flex-1 gap-6 py-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <SourcePanel source={source} onReingest={() => void reingest()} onNewChat={() => void startNew()} />
          <ModelPool compact />
          <Button
            variant="danger"
            size="sm"
            className="w-full"
            onClick={async () => {
              if (!confirm("Delete this source and its conversations?")) return;
              await api.sources.remove(source.id);
              router.push("/");
            }}
          >
            <Icon name="trash" className="h-3.5 w-3.5" /> Delete source
          </Button>
        </div>
        <div className="flex min-h-[70vh] flex-col lg:h-[calc(100vh-7.5rem)]">
          {conversationError && <p className="mb-2 text-sm text-rose-600 dark:text-rose-400">{conversationError}</p>}
          <ChatPanel
            title={source.title}
            kind={source.kind}
            ready={ready}
            pending={<PendingNotice source={source} />}
            conversationId={conversation?.id ?? null}
            modes={modes}
          />
        </div>
      </div>
    </AppShell>
  );
}

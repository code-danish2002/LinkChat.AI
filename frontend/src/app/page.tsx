"use client";

import { useRouter } from "next/navigation";

import { CollectionList } from "@/components/collections/CollectionList";
import { AppShell } from "@/components/layout/AppShell";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { ModelPool } from "@/components/models/ModelPool";
import { SourceForm } from "@/components/sources/SourceForm";
import { SourceList } from "@/components/sources/SourceList";
import { Icon, type IconName } from "@/components/ui/Icon";
import { useCatalogue } from "@/hooks/useCatalogue";
import { useCollections } from "@/hooks/useCollections";
import { useSources } from "@/hooks/useSources";

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: "globe", title: "Any link or file", text: "Articles, Wikipedia, product pages, PDFs, Word, slides, spreadsheets and EPUB." },
  { icon: "mic", title: "Video and audio", text: "YouTube captions and uploaded recordings, with citations back to the timestamp." },
  { icon: "wand", title: "One-click tasks", text: "Summarise, outline, extract tables, make flashcards or quiz yourself, no typing." },
  { icon: "sparkles", title: "Open models, no lock-in", text: "A pool of open-weight models. If one fails or rate-limits, the next answers." },
];

export default function HomePage() {
  const router = useRouter();
  const { sources, loading, error, remove, add } = useSources();
  const { capabilities } = useCatalogue();
  const collections = useCollections();

  return (
    <AppShell right={<ThemeToggle />}>
      <section className="mx-auto w-full max-w-3xl pb-10 pt-16 text-center sm:pt-24">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          Chat with any{" "}
          <span className="bg-gradient-to-r from-indigo-600 to-violet-500 bg-clip-text text-transparent">link</span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-base text-slate-600 dark:text-slate-400">
          Paste a URL or drop a file. LinkChat reads it, indexes it, and answers with citations from the source.
        </p>
        <div className="mt-8">
          <SourceForm
            capabilities={capabilities}
            onCreated={(source) => {
              add(source);
              router.push(`/s/${source.id}`);
            }}
          />
        </div>
        <ul className="mt-10 grid gap-4 text-left sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((feature) => (
            <li
              key={feature.title}
              className="rounded-xl bg-white/70 p-4 ring-1 ring-slate-200 dark:bg-slate-900/60 dark:ring-slate-800"
            >
              <Icon name={feature.icon} className="h-5 w-5 text-indigo-500" />
              <p className="mt-2 text-sm font-semibold">{feature.title}</p>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{feature.text}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="mx-auto grid w-full max-w-5xl gap-8 pb-16 lg:grid-cols-[1fr_320px]">
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Recent sources</h2>
          <SourceList sources={sources} loading={loading} error={error} onDelete={(id) => void remove(id)} />
        </div>
        <div className="space-y-8">
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Collections</h2>
            <CollectionList
              collections={collections.collections}
              loading={collections.loading}
              error={collections.error}
              onCreate={collections.create}
              onDelete={(id) => void collections.remove(id)}
            />
          </div>
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Models</h2>
            <ModelPool compact />
          </div>
        </div>
      </section>
    </AppShell>
  );
}

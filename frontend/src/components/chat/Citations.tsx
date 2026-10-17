"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/ui/Icon";
import type { Citation } from "@/lib/api/types";

export function Citations({ citations, highlight }: { citations: Citation[]; highlight: number | null }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const refs = useRef<Record<number, HTMLLIElement | null>>({});

  useEffect(() => {
    if (highlight === null) return;
    setOpen(true);
    setExpanded(highlight);
    const timer = setTimeout(
      () => refs.current[highlight]?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
      50,
    );
    return () => clearTimeout(timer);
  }, [highlight]);

  if (citations.length === 0) return null;

  return (
    <div className="mt-2 border-t border-slate-200/70 pt-2 dark:border-slate-700/60">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
      >
        <Icon name="quote" className="h-3.5 w-3.5" />
        {citations.length} source excerpt{citations.length === 1 ? "" : "s"}
        <Icon name="chevron" className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <ol className="mt-2 space-y-1.5">
          {citations.map((citation) => {
            const active = expanded === citation.index;
            const flash = highlight === citation.index;
            return (
              <li
                key={citation.chunk_id}
                ref={(element) => {
                  refs.current[citation.index] = element;
                }}
                className={`rounded-lg text-xs ring-1 transition ${
                  flash
                    ? "bg-indigo-50/60 ring-indigo-400 dark:bg-indigo-950/30"
                    : "bg-slate-50 ring-slate-200 dark:bg-slate-800/60 dark:ring-slate-700"
                }`}
              >
                <button
                  type="button"
                  onClick={() => setExpanded(active ? null : citation.index)}
                  className="flex w-full items-start gap-2 p-2 text-left"
                >
                  <span className="mt-px inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded bg-indigo-600 px-1 text-[10px] font-semibold text-white">
                    {citation.index}
                  </span>
                  <span className={`flex-1 text-slate-700 dark:text-slate-300 ${active ? "whitespace-pre-wrap" : "line-clamp-2"}`}>
                    {citation.text}
                  </span>
                </button>
                <div className="flex flex-wrap items-center gap-3 px-2 pb-2 text-[11px] text-slate-500 dark:text-slate-400">
                  {citation.metadata.source_title && (
                    <span className="max-w-[12rem] truncate font-medium text-slate-600 dark:text-slate-300">
                      {citation.metadata.source_title}
                    </span>
                  )}
                  {citation.metadata.timestamp &&
                    (typeof citation.metadata.link === "string" ? (
                      <a
                        href={citation.metadata.link}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-indigo-600 hover:underline dark:text-indigo-400"
                      >
                        <Icon name="clock" className="h-3 w-3" /> {citation.metadata.timestamp}
                      </a>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        <Icon name="clock" className="h-3 w-3" /> {citation.metadata.timestamp}
                      </span>
                    ))}
                  {typeof citation.metadata.chunk_index === "number" && (
                    <span>chunk #{citation.metadata.chunk_index + 1}</span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

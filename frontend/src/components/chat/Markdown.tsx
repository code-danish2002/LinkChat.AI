"use client";

import { Fragment, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Matches [3] and also the full-width 【3】 some models emit.
const CITATION_RE = /\[(\d{1,2})\]|【(\d{1,2})】/g;

/** Turns "[3]" markers inside text nodes into clickable citation chips. */
function withCitations(children: ReactNode, onCite?: (index: number) => void): ReactNode {
  if (typeof children === "string") {
    const parts: ReactNode[] = [];
    let last = 0;
    for (const match of children.matchAll(CITATION_RE)) {
      const start = match.index ?? 0;
      if (start > last) parts.push(children.slice(last, start));
      const index = Number(match[1] ?? match[2]);
      parts.push(
        <button
          key={`${start}-${index}`}
          type="button"
          onClick={() => onCite?.(index)}
          className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-indigo-100 px-1 align-text-top text-[10px] font-semibold text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-900/60 dark:text-indigo-200 dark:hover:bg-indigo-800"
          title={`Show excerpt ${index}`}
        >
          {index}
        </button>,
      );
      last = start + match[0].length;
    }
    if (last < children.length) parts.push(children.slice(last));
    return parts.length ? parts.map((p, i) => <Fragment key={i}>{p}</Fragment>) : children;
  }
  if (Array.isArray(children)) return children.map((child, i) => <Fragment key={i}>{withCitations(child, onCite)}</Fragment>);
  return children;
}

export function Markdown({ content, onCite }: { content: string; onCite?: (index: number) => void }) {
  const components: Components = {
    p: ({ children }) => <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{withCitations(children, onCite)}</p>,
    li: ({ children }) => <li className="my-0.5">{withCitations(children, onCite)}</li>,
    ul: ({ children }) => <ul className="my-2 list-disc space-y-0.5 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="my-2 list-decimal space-y-0.5 pl-5">{children}</ol>,
    h1: ({ children }) => <h3 className="mb-1 mt-3 text-base font-semibold">{children}</h3>,
    h2: ({ children }) => <h3 className="mb-1 mt-3 text-base font-semibold">{children}</h3>,
    h3: ({ children }) => <h4 className="mb-1 mt-3 text-sm font-semibold">{children}</h4>,
    strong: ({ children }) => <strong className="font-semibold">{withCitations(children, onCite)}</strong>,
    em: ({ children }) => <em>{withCitations(children, onCite)}</em>,
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noreferrer" className="text-indigo-600 underline decoration-indigo-300 underline-offset-2 dark:text-indigo-400">
        {children}
      </a>
    ),
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-slate-300 pl-3 text-slate-600 dark:border-slate-600 dark:text-slate-300">{children}</blockquote>
    ),
    code: ({ className, children }) => {
      const block = Boolean(className) || String(children).includes("\n");
      return block ? (
        <code className={`${className ?? ""} block overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100 dark:bg-black/60`}>{children}</code>
      ) : (
        <code className="rounded bg-slate-200/70 px-1 py-0.5 font-mono text-[0.85em] dark:bg-slate-700/70">{children}</code>
      );
    },
    pre: ({ children }) => <pre className="my-2">{children}</pre>,
    table: ({ children }) => (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-left text-xs">{children}</table>
      </div>
    ),
    th: ({ children }) => <th className="border-b border-slate-300 px-2 py-1 font-semibold dark:border-slate-600">{children}</th>,
    td: ({ children }) => <td className="border-b border-slate-200 px-2 py-1 align-top dark:border-slate-700">{withCitations(children, onCite)}</td>,
    hr: () => <hr className="my-3 border-slate-200 dark:border-slate-700" />,
  };

  return (
    <div className="text-[15px]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

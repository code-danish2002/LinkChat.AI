"use client";

import { Icon, type IconName } from "@/components/ui/Icon";
import type { ChatModeId, Mode } from "@/lib/api/types";

const ICONS: Record<ChatModeId, IconName> = {
  qa: "help",
  summary: "sparkles",
  key_points: "list",
  outline: "clock",
  action_items: "check",
  extract: "table",
  flashcards: "cards",
  quiz: "target",
  simplify: "wand",
  translate: "language",
  fact_check: "shield",
  blog_post: "pen",
};

export function ModeBar({
  modes,
  active,
  disabled,
  onSelect,
}: {
  modes: Mode[];
  active: ChatModeId;
  disabled: boolean;
  onSelect: (mode: Mode) => void;
}) {
  if (modes.length === 0) return null;
  return (
    <div className="scrollbar-thin -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1" role="tablist" aria-label="Mode">
      {modes.map((mode) => {
        const selected = mode.id === active;
        return (
          <button
            key={mode.id}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={disabled}
            title={mode.description}
            onClick={() => onSelect(mode)}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
              selected
                ? "bg-indigo-600 text-white shadow-sm"
                : "bg-white text-slate-600 ring-1 ring-slate-200 hover:text-slate-900 hover:ring-indigo-300 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-700 dark:hover:text-white"
            }`}
          >
            <Icon name={ICONS[mode.id] ?? "sparkles"} className="h-3.5 w-3.5" />
            {mode.label}
          </button>
        );
      })}
    </div>
  );
}

export function modeIcon(id: ChatModeId): IconName {
  return ICONS[id] ?? "sparkles";
}

import Link from "next/link";
import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";

export function AppShell({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-sm">
              <Icon name="link" className="h-4 w-4" />
            </span>
            <span>LinkChat</span>
          </Link>
          <div className="flex items-center gap-3 text-sm text-slate-500 dark:text-slate-400">{right}</div>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 sm:px-6">{children}</main>
    </div>
  );
}

import Link from "next/link";

import { AppShell } from "@/components/layout/AppShell";

export default function NotFound() {
  return (
    <AppShell>
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="text-5xl font-bold text-slate-300 dark:text-slate-700">404</p>
        <p className="text-slate-600 dark:text-slate-400">That page does not exist.</p>
        <Link href="/" className="text-indigo-600 hover:underline dark:text-indigo-400">Go home</Link>
      </div>
    </AppShell>
  );
}

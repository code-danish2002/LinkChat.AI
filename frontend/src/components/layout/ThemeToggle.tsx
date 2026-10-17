"use client";

import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className="rounded-lg px-2 py-1 text-xs font-medium text-slate-500 ring-1 ring-slate-200 hover:text-slate-900 dark:text-slate-400 dark:ring-slate-700 dark:hover:text-white"
      aria-label="Toggle theme"
    >
      {dark ? "Light" : "Dark"}
    </button>
  );
}

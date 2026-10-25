"use client";

import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { Icon } from "@/components/ui/Icon";

/** Header control: "Sign in" for guests, a small menu for signed-in users. */
export function AccountMenu() {
  const { me, loading, isGuest, openLogin, logout, forgetGuest, deleteAccount } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  if (loading) return <span className="h-8 w-20 animate-pulse rounded-lg bg-slate-200/70 dark:bg-slate-800/70" />;

  if (isGuest) {
    return (
      <div ref={ref} className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-slate-600 ring-1 ring-slate-200 hover:text-slate-900 dark:text-slate-300 dark:ring-slate-700 dark:hover:text-white"
        >
          <Icon name="shield" className="h-3.5 w-3.5" /> Guest
          <Icon name="chevron" className="h-3 w-3" />
        </button>
        {open && (
          <Menu>
            <p className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400">
              Your sources are kept {me?.guest_ttl_hours ?? 24} hours after your last visit, then deleted.
            </p>
            <MenuItem
              icon="sparkles"
              onClick={() => {
                setOpen(false);
                openLogin();
              }}
            >
              Sign in to keep them
            </MenuItem>
            <MenuItem
              icon="trash"
              danger
              onClick={() => {
                if (confirm("Delete everything added as this guest, right now?")) void forgetGuest();
              }}
            >
              Forget me now
            </MenuItem>
          </Menu>
        )}
      </div>
    );
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 max-w-[11rem] items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-slate-700 ring-1 ring-slate-200 hover:text-slate-900 dark:text-slate-200 dark:ring-slate-700 dark:hover:text-white"
        title={me?.user?.email}
      >
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-semibold uppercase text-white">
          {me?.user?.display_name.charAt(0)}
        </span>
        <span className="truncate">{me?.user?.display_name}</span>
        <Icon name="chevron" className="h-3 w-3 shrink-0" />
      </button>
      {open && (
        <Menu>
          <p className="truncate px-3 py-2 text-xs text-slate-500 dark:text-slate-400">{me?.user?.email}</p>
          <MenuItem icon="close" onClick={() => void logout()}>
            Sign out
          </MenuItem>
          <MenuItem
            icon="trash"
            danger
            onClick={() => {
              if (confirm("Delete your account and every source, collection and chat it owns? This cannot be undone."))
                void deleteAccount();
            }}
          >
            Delete account
          </MenuItem>
        </Menu>
      )}
    </div>
  );
}

function Menu({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute right-0 top-10 z-30 w-64 overflow-hidden rounded-xl bg-white py-1 shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
      {children}
    </div>
  );
}

function MenuItem({
  icon,
  danger,
  onClick,
  children,
}: {
  icon: "sparkles" | "trash" | "close";
  danger?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800 ${
        danger ? "text-rose-600 dark:text-rose-400" : "text-slate-700 dark:text-slate-200"
      }`}
    >
      <Icon name={icon} className="h-4 w-4" />
      {children}
    </button>
  );
}

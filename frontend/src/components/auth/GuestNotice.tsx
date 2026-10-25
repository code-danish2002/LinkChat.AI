"use client";

import { useAuth } from "@/components/auth/AuthProvider";
import { Icon } from "@/components/ui/Icon";

/** One line under the hero telling guests what happens to their data. */
export function GuestNotice() {
  const { me, loading, isGuest, openLogin } = useAuth();
  if (loading || !isGuest) return null;
  const hours = me?.guest_ttl_hours ?? 24;
  return (
    <p className="mx-auto mt-4 inline-flex flex-wrap items-center justify-center gap-x-1.5 rounded-full bg-amber-50 px-3 py-1.5 text-xs text-amber-800 ring-1 ring-amber-200 dark:bg-amber-950/30 dark:text-amber-200 dark:ring-amber-900">
      <Icon name="clock" className="h-3.5 w-3.5" />
      You are browsing as a guest. Sources are deleted {hours} hours after your last visit.
      <button type="button" onClick={openLogin} className="font-semibold underline-offset-2 hover:underline">
        Sign in to keep them
      </button>
    </p>
  );
}

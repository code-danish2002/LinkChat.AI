"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { LoginDialog } from "@/components/auth/LoginDialog";
import { api, ApiError } from "@/lib/api/client";
import type { Me } from "@/lib/api/types";
import { AUTH_CHANGED_EVENT, resetGuestId, setToken } from "@/lib/auth";

interface AuthContextValue {
  me: Me | null;
  loading: boolean;
  error: string | null;
  isGuest: boolean;
  openLogin: () => void;
  logout: () => Promise<void>;
  forgetGuest: () => Promise<void>;
  deleteAccount: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Loads who the browser is and offers sign-in; everything after a change reloads so every list refetches. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.auth.me());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onChange = () => void refresh();
    window.addEventListener(AUTH_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, onChange);
  }, [refresh]);

  const reloadAs = useCallback((token: string | null, newGuest: boolean) => {
    setToken(token);
    if (newGuest) resetGuestId();
    window.location.assign("/");
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      // A fresh guest id, so the next visitor on this device does not inherit anything.
      reloadAs(null, true);
    }
  }, [reloadAs]);

  const forgetGuest = useCallback(async () => {
    await api.auth.forgetGuest();
    reloadAs(null, true);
  }, [reloadAs]);

  const deleteAccount = useCallback(async () => {
    await api.auth.deleteAccount();
    reloadAs(null, true);
  }, [reloadAs]);

  const value = useMemo<AuthContextValue>(
    () => ({
      me,
      loading,
      error,
      isGuest: me?.kind !== "user",
      openLogin: () => setLoginOpen(true),
      logout,
      forgetGuest,
      deleteAccount,
      refresh,
    }),
    [me, loading, error, logout, forgetGuest, deleteAccount, refresh],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {loginOpen && (
        <LoginDialog
          onClose={() => setLoginOpen(false)}
          onSignedIn={(session) => {
            setLoginOpen(false);
            // Keep the guest id: the server has just moved its data into the account.
            reloadAs(session.token, false);
          }}
        />
      )}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

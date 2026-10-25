"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { api, ApiError } from "@/lib/api/client";
import type { SessionInfo } from "@/lib/api/types";

type Step = "email" | "code";

/** Two steps: email (plus a name for new accounts), then the six-digit code. */
export function LoginDialog({ onClose, onSignedIn }: { onClose: () => void; onSignedIn: (session: SessionInfo) => void }) {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [needsName, setNeedsName] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
  }, [step]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function requestCode(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.auth.requestCode(email.trim(), name.trim() || undefined);
      setStep("code");
      setHint(
        result.debug_code
          ? `Development server: your code is ${result.debug_code}.`
          : `We emailed a code to ${result.email}. Check spam if it does not arrive in a minute.`,
      );
    } catch (err) {
      if (err instanceof ApiError && err.details?.needs_name) {
        setNeedsName(true);
        setError("New here? Add your name to create an account.");
      } else {
        setError(err instanceof ApiError ? err.message : "Could not send the code.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.auth.verify(email.trim(), code.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not verify the code.");
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="login-title"
    >
      <div
        className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-800"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 id="login-title" className="text-lg font-semibold">
              {step === "email" ? "Sign in or create an account" : "Enter your code"}
            </h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              {step === "email"
                ? "No password. We email you a six-digit code. Everything you added as a guest comes with you."
                : hint}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800"
            aria-label="Close"
          >
            <Icon name="close" className="h-4 w-4" />
          </button>
        </div>

        {step === "email" ? (
          <form onSubmit={requestCode} className="mt-5 space-y-3">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Email</span>
              <input
                ref={first}
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className={input}
                placeholder="you@example.com"
              />
            </label>
            {needsName && (
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Your name</span>
                <input
                  type="text"
                  required
                  autoComplete="name"
                  maxLength={80}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className={input}
                  placeholder="Ada Lovelace"
                />
              </label>
            )}
            {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? <Icon name="spinner" className="h-4 w-4" /> : <Icon name="send" className="h-4 w-4" />}
              {needsName ? "Create account and send code" : "Send code"}
            </Button>
          </form>
        ) : (
          <form onSubmit={verify} className="mt-5 space-y-3">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Six-digit code</span>
              <input
                ref={first}
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                autoComplete="one-time-code"
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                className={`${input} text-center text-lg tracking-[0.4em]`}
                placeholder="123456"
              />
            </label>
            {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy || code.length !== 6}>
              {busy ? <Icon name="spinner" className="h-4 w-4" /> : <Icon name="check" className="h-4 w-4" />}
              Sign in
            </Button>
            <button
              type="button"
              className="w-full text-center text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              onClick={() => {
                setStep("email");
                setCode("");
                setError(null);
              }}
            >
              Use a different email or resend the code
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

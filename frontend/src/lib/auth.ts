/**
 * Who this browser is, as far as the API is concerned.
 *
 * A guest id is made up once per browser and kept in localStorage; the server
 * owns everything created under it until it has been quiet for a day. Signing
 * in stores a bearer token, which takes precedence over the guest id. Both are
 * sent as headers on every request (cookies would be third-party here, since
 * the API lives on another domain).
 */

const GUEST_KEY = "linkchat.guest";
const TOKEN_KEY = "linkchat.session";
export const AUTH_CHANGED_EVENT = "linkchat:auth-changed";

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function randomId(): string {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

let memoryGuest: string | null = null;

/** The guest id for this browser, created on first use. */
export function getGuestId(): string {
  const store = storage();
  const existing = store?.getItem(GUEST_KEY) ?? memoryGuest;
  if (existing && /^gst_[A-Za-z0-9_-]{8,64}$/.test(existing)) return existing;
  const fresh = `gst_${randomId()}`;
  memoryGuest = fresh;
  store?.setItem(GUEST_KEY, fresh);
  return fresh;
}

/** Forget the current guest id and start a new one. */
export function resetGuestId(): string {
  storage()?.removeItem(GUEST_KEY);
  memoryGuest = null;
  return getGuestId();
}

export function getToken(): string | null {
  return storage()?.getItem(TOKEN_KEY) ?? null;
}

export function setToken(token: string | null): void {
  const store = storage();
  if (!store) return;
  if (token) store.setItem(TOKEN_KEY, token);
  else store.removeItem(TOKEN_KEY);
  notifyAuthChanged();
}

export function notifyAuthChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

/** Headers identifying the caller. The guest id is always sent so a sign-in can adopt it. */
export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "X-Guest-Id": getGuestId() };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

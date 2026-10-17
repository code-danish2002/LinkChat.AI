import { readSse } from "./sse";
import type {
  ApiErrorBody,
  ChatModeId,
  ChatStreamEvent,
  Collection,
  Conversation,
  Health,
  Message,
  Mode,
  ModelPool,
  Source,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const API = `${API_BASE}/api/v1`;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const unreachable = () =>
  new ApiError(0, "network_error", `Cannot reach the API at ${API_BASE}. Is the backend running?`);

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  const headers = init.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...(init.headers ?? {}) };
  try {
    response = await fetch(`${API}${path}`, { ...init, headers });
  } catch {
    throw unreachable();
  }
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiErrorBody> & { detail?: unknown };
    if (body.error) return new ApiError(response.status, body.error.code, body.error.message, body.error.details);
    if (body.detail) {
      const detail = Array.isArray(body.detail)
        ? body.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ")
        : String(body.detail);
      return new ApiError(response.status, "validation_error", detail);
    }
  } catch {
    /* fall through to a generic message */
  }
  return new ApiError(response.status, "http_error", `Request failed with HTTP ${response.status}.`);
}

export const api = {
  health: () => request<Health>("/health"),
  models: () => request<ModelPool>("/models"),
  modes: () => request<Mode[]>("/modes"),

  sources: {
    list: (limit = 50) => request<Source[]>(`/sources?limit=${limit}`),
    get: (id: string) => request<Source>(`/sources/${encodeURIComponent(id)}`),
    create: (url: string) => request<Source>("/sources", { method: "POST", body: JSON.stringify({ url }) }),
    upload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<Source>("/sources/upload", { method: "POST", body: form });
    },
    reingest: (id: string) => request<Source>(`/sources/${encodeURIComponent(id)}/reingest`, { method: "POST" }),
    remove: (id: string) => request<void>(`/sources/${encodeURIComponent(id)}`, { method: "DELETE" }),
    conversations: (id: string) => request<Conversation[]>(`/sources/${encodeURIComponent(id)}/conversations`),
  },

  collections: {
    list: (limit = 50) => request<Collection[]>(`/collections?limit=${limit}`),
    get: (id: string) => request<Collection>(`/collections/${encodeURIComponent(id)}`),
    create: (title: string, source_ids: string[] = []) =>
      request<Collection>("/collections", { method: "POST", body: JSON.stringify({ title, source_ids }) }),
    update: (id: string, patch: { title?: string; source_ids?: string[] }) =>
      request<Collection>(`/collections/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    addSource: (id: string, sourceId: string) =>
      request<Collection>(`/collections/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}`, { method: "POST" }),
    removeSource: (id: string, sourceId: string) =>
      request<Collection>(`/collections/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" }),
    sources: (id: string) => request<Source[]>(`/collections/${encodeURIComponent(id)}/sources`),
    conversations: (id: string) => request<Conversation[]>(`/collections/${encodeURIComponent(id)}/conversations`),
    remove: (id: string) => request<void>(`/collections/${encodeURIComponent(id)}`, { method: "DELETE" }),
  },

  conversations: {
    create: (target: { source_id?: string; collection_id?: string; title?: string }) =>
      request<Conversation>("/conversations", { method: "POST", body: JSON.stringify(target) }),
    get: (id: string) => request<Conversation>(`/conversations/${encodeURIComponent(id)}`),
    remove: (id: string) => request<void>(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
    messages: (id: string) => request<Message[]>(`/conversations/${encodeURIComponent(id)}/messages`),

    /** Send a message and stream the answer back as typed events. */
    async *send(id: string, content: string, mode: ChatModeId, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
      let response: Response;
      try {
        response = await fetch(`${API}/conversations/${encodeURIComponent(id)}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ content, mode }),
          signal,
        });
      } catch (error) {
        if ((error as Error).name === "AbortError") return;
        throw unreachable();
      }
      if (!response.ok) throw await toApiError(response);

      for await (const message of readSse(response, signal)) {
        yield { type: message.event, data: JSON.parse(message.data) } as ChatStreamEvent;
      }
    },
  },
};

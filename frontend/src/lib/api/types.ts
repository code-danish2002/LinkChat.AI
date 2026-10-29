export type SourceKind = "youtube" | "web" | "pdf" | "text" | "docx" | "pptx" | "xlsx" | "epub" | "audio";
export type SourceStatus = "pending" | "processing" | "ready" | "failed";

export type ChatModeId =
  | "qa"
  | "summary"
  | "key_points"
  | "outline"
  | "action_items"
  | "extract"
  | "flashcards"
  | "quiz"
  | "simplify"
  | "translate"
  | "fact_check"
  | "blog_post";

export interface Mode {
  id: ChatModeId;
  label: string;
  description: string;
  scope: "retrieval" | "document";
  needs_input: boolean;
  input_label: string | null;
  input_placeholder: string | null;
  default_prompt: string;
}

export interface Source {
  id: string;
  /** The shared content this source points at; two owners adding one URL share it. */
  document_id: string;
  url: string;
  kind: SourceKind;
  title: string;
  status: SourceStatus;
  error: string | null;
  chunk_count: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Collection {
  id: string;
  title: string;
  source_ids: string[];
  source_count: number;
  ready_count: number;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  source_id: string | null;
  collection_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  index: number;
  chunk_id: string;
  text: string;
  score: number;
  metadata: Record<string, unknown> & {
    start?: number;
    end?: number;
    timestamp?: string;
    link?: string;
    chunk_index?: number;
    source_id?: string;
    source_title?: string;
  };
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  mode: ChatModeId;
  citations: Citation[];
  created_at: string;
}

export interface ModelStatus {
  id: string;
  provider: string;
  model: string;
  display_name: string;
  priority: number;
  active: boolean;
  state: "closed" | "open" | "half_open" | "inactive";
  retry_in_seconds: number | null;
  successes: number;
  failures: number;
  last_error: string | null;
  average_latency_ms: number | null;
  available_upstream: boolean | null;
}

export interface ModelPool {
  active_count: number;
  total_count: number;
  models: ModelStatus[];
}

export interface Capabilities {
  uploads: boolean;
  audio_transcription: boolean;
  javascript_rendering: boolean;
  collections: boolean;
  /** Spoken questions are accepted (needs a speech-to-text provider on the server). */
  voice: boolean;
  upload_extensions: string[];
  max_upload_mb: number;
  /** "resend" means codes arrive by email; "console" means they are only logged on the server. */
  email_login: string;
  guest_ttl_hours: number;
  session_ttl_hours: number;
}

export interface Health {
  status: string;
  version: string;
  embedding_model: string;
  active_models: number;
  database: "ok" | "unreachable";
  supported_sources: string[];
  capabilities: Capabilities;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

export interface Transcript {
  text: string;
  language: string | null;
  duration_seconds: number | null;
}

export type ChatStreamEvent =
  | { type: "transcript"; data: Transcript }
  | { type: "citations"; data: Citation[] }
  | { type: "progress"; data: string }
  | { type: "model"; data: string }
  | { type: "token"; data: string }
  | {
      type: "done";
      data: {
        user_message_id: string;
        assistant_message_id: string;
        model: string | null;
        mode: ChatModeId;
        /** Language name of the question ("Hindi"), for picking a voice; null when unsure. */
        language: string | null;
      };
    }
  | { type: "error"; data: string };

export interface User {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface Me {
  kind: "guest" | "user";
  owner_id: string;
  user: User | null;
  session_expires_at: string | null;
  guest_ttl_hours: number;
}

export interface RequestCodeResult {
  sent: boolean;
  email: string;
  expires_at: string;
  delivery: string;
  /** Only present when the server logs codes instead of emailing them (local development). */
  debug_code: string | null;
}

export interface SessionInfo {
  token: string;
  expires_at: string;
  user: User;
}

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
  upload_extensions: string[];
  max_upload_mb: number;
}

export interface Health {
  status: string;
  version: string;
  embedding_model: string;
  active_models: number;
  supported_sources: string[];
  capabilities: Capabilities;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

export type ChatStreamEvent =
  | { type: "citations"; data: Citation[] }
  | { type: "progress"; data: string }
  | { type: "model"; data: string }
  | { type: "token"; data: string }
  | { type: "done"; data: { user_message_id: string; assistant_message_id: string; model: string | null; mode: ChatModeId } }
  | { type: "error"; data: string };

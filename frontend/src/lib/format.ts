import type { SourceKind } from "./api/types";

export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  if (Number.isNaN(seconds)) return "";
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return new Date(iso).toLocaleDateString();
}

const UPLOAD_SCHEME = "upload://";

export function isUpload(url: string): boolean {
  return url.startsWith(UPLOAD_SCHEME);
}

export function hostOf(url: string): string {
  if (isUpload(url)) return "uploaded file";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

const KIND_LABELS: Record<SourceKind, string> = {
  youtube: "YouTube",
  web: "Web page",
  pdf: "PDF",
  text: "Text",
  docx: "Word",
  pptx: "Slides",
  xlsx: "Spreadsheet",
  epub: "Book",
  audio: "Audio",
};

export function kindLabel(kind: SourceKind): string {
  return KIND_LABELS[kind] ?? kind;
}

export function modelLabel(modelId: string | null | undefined): string {
  if (!modelId) return "";
  const [provider, ...rest] = modelId.split("/");
  const model = rest.join("/");
  const short = model.split("/").pop() ?? model;
  return `${short} · ${provider}`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours} h ${minutes} min`;
  if (minutes) return `${minutes} min`;
  return `${total} s`;
}

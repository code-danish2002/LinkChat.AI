/**
 * Voice plumbing that lives entirely in the browser.
 *
 * - `encodeWav` turns the 16 kHz samples the voice detector hands back into a
 *   WAV file the backend's Whisper step accepts, so every browser sends the
 *   same format.
 * - `Speaker` reads text aloud with the browser's own speech synthesis,
 *   sentence by sentence as tokens stream in, so the first words are heard
 *   while the rest is still being generated.
 * - `speakable` strips citation markers and Markdown that would otherwise be
 *   read out loud as "open bracket one close bracket".
 */

export const VOICE_SAMPLE_RATE = 16000;

/** PCM 16-bit mono WAV from float samples in [-1, 1]. */
export function encodeWav(samples: Float32Array, sampleRate = VOICE_SAMPLE_RATE): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function supportsSpeechOutput(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

export function supportsSpeechInput(): boolean {
  return (
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof window !== "undefined" &&
    (window.isSecureContext || location.hostname === "localhost")
  );
}

/** Plain prose for the ear: no citations, no Markdown, no code. */
export function speakable(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\[(\d{1,3})\]/g, "")
    .replace(/【\d{1,3}】/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/\|/g, " ")
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, "$1")
    .replace(/[*_~#]/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\s*\n\s*/g, "\n")
    .trim();
}

// ---------------------------------------------------------------- language

const NAME_TO_TAG: Record<string, string> = {
  english: "en-US",
  hindi: "hi-IN",
  spanish: "es-ES",
  french: "fr-FR",
  german: "de-DE",
  portuguese: "pt-BR",
  italian: "it-IT",
  dutch: "nl-NL",
  polish: "pl-PL",
  turkish: "tr-TR",
  russian: "ru-RU",
  ukrainian: "uk-UA",
  arabic: "ar-SA",
  urdu: "ur-PK",
  bengali: "bn-IN",
  tamil: "ta-IN",
  telugu: "te-IN",
  marathi: "mr-IN",
  gujarati: "gu-IN",
  kannada: "kn-IN",
  malayalam: "ml-IN",
  punjabi: "pa-IN",
  japanese: "ja-JP",
  chinese: "zh-CN",
  korean: "ko-KR",
  thai: "th-TH",
  vietnamese: "vi-VN",
  indonesian: "id-ID",
  hebrew: "he-IL",
  greek: "el-GR",
  danish: "da-DK",
  norwegian: "nb-NO",
  swedish: "sv-SE",
  finnish: "fi-FI",
};

const SCRIPT_TAGS: [RegExp, string][] = [
  [/[ऀ-ॿ]/, "hi-IN"],
  [/[ঀ-৿]/, "bn-IN"],
  [/[਀-੿]/, "pa-IN"],
  [/[઀-૿]/, "gu-IN"],
  [/[஀-௿]/, "ta-IN"],
  [/[ఀ-౿]/, "te-IN"],
  [/[ಀ-೿]/, "kn-IN"],
  [/[ഀ-ൿ]/, "ml-IN"],
  [/[؀-ۿ]/, "ar-SA"],
  [/[֐-׿]/, "he-IL"],
  [/[Ѐ-ӿ]/, "ru-RU"],
  [/[Ͱ-Ͽ]/, "el-GR"],
  [/[฀-๿]/, "th-TH"],
  [/[぀-ヿ]/, "ja-JP"],
  [/[가-힯]/, "ko-KR"],
  [/[一-鿿]/, "zh-CN"],
];

/**
 * A BCP-47 tag for choosing a voice. `hint` can be a language name from the
 * backend ("Hindi"), a Whisper code or name ("hi", "hindi"), or nothing, in
 * which case the text's script decides, then the browser's own language.
 */
export function languageTag(hint?: string | null, text?: string): string {
  const key = (hint ?? "").trim().toLowerCase();
  if (key) {
    if (NAME_TO_TAG[key]) return NAME_TO_TAG[key];
    const byCode = Object.values(NAME_TO_TAG).find((tag) => tag.toLowerCase().startsWith(key + "-"));
    if (byCode) return byCode;
    if (/^[a-z]{2,3}(-[a-z]{2,4})?$/i.test(key)) return key;
  }
  for (const [pattern, tag] of SCRIPT_TAGS) if (text && pattern.test(text)) return tag;
  return typeof navigator !== "undefined" && navigator.language ? navigator.language : "en-US";
}

let voicesCache: SpeechSynthesisVoice[] = [];

function voices(): SpeechSynthesisVoice[] {
  if (!supportsSpeechOutput()) return [];
  const list = window.speechSynthesis.getVoices();
  if (list.length) voicesCache = list;
  return voicesCache;
}

if (supportsSpeechOutput()) {
  window.speechSynthesis.addEventListener?.("voiceschanged", () => voices());
  voices();
}

export function pickVoice(tag: string): SpeechSynthesisVoice | null {
  const all = voices();
  if (!all.length) return null;
  const wanted = tag.toLowerCase();
  const prefix = wanted.split("-")[0];
  const score = (voice: SpeechSynthesisVoice) => {
    const lang = voice.lang.toLowerCase().replace("_", "-");
    let points = 0;
    if (lang === wanted) points += 4;
    else if (lang.startsWith(prefix + "-") || lang === prefix) points += 2;
    else return -1;
    if (/natural|enhanced|premium|neural|google/i.test(voice.name)) points += 1;
    if (voice.localService) points += 0.5;
    return points;
  };
  let best: SpeechSynthesisVoice | null = null;
  let bestScore = -1;
  for (const voice of all) {
    const s = score(voice);
    if (s > bestScore) {
      best = voice;
      bestScore = s;
    }
  }
  return best;
}

// ---------------------------------------------------------------- speaker

const SENTENCE_END = /[.!?।]["')\]]?(?=\s|$)|\n/;

/**
 * Reads streamed text aloud. Feed it token deltas; it speaks each sentence as
 * soon as the sentence is complete, which keeps the browser's utterance queue
 * short (Chrome cuts off long utterances) and gets sound out early.
 */
export class Speaker {
  private buffer = "";
  private pending = 0;
  private lang = "en-US";
  private listeners = new Set<(speaking: boolean) => void>();
  private active = false;

  get speaking(): boolean {
    return this.active;
  }

  onChange(listener: (speaking: boolean) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setLanguage(tag: string): void {
    this.lang = tag;
  }

  /** Append streamed text; complete sentences are spoken right away. */
  feed(delta: string): void {
    this.buffer += delta;
    let match: RegExpExecArray | null;
    while ((match = SENTENCE_END.exec(this.buffer)) !== null) {
      const end = match.index + match[0].length;
      const sentence = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end);
      this.say(sentence);
    }
    // A very long run without punctuation is spoken at a clause boundary.
    if (this.buffer.length > 240) {
      const cut = this.buffer.lastIndexOf(", ");
      if (cut > 80) {
        this.say(this.buffer.slice(0, cut + 1));
        this.buffer = this.buffer.slice(cut + 1);
      }
    }
  }

  /** Speak whatever is left; call when the stream is done. */
  flush(): void {
    const rest = this.buffer;
    this.buffer = "";
    this.say(rest);
    if (this.pending === 0) this.setActive(false);
  }

  /** Speak a whole text from scratch, stopping anything in progress. */
  speakAll(text: string, lang?: string): void {
    this.stop();
    if (lang) this.lang = lang;
    this.feed(text);
    this.flush();
  }

  stop(): void {
    this.buffer = "";
    this.pending = 0;
    if (supportsSpeechOutput()) window.speechSynthesis.cancel();
    this.setActive(false);
  }

  private say(raw: string): void {
    const text = speakable(raw);
    if (!text || !supportsSpeechOutput()) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = this.lang;
    const voice = pickVoice(this.lang);
    if (voice) utterance.voice = voice;
    utterance.rate = 1.0;
    this.pending += 1;
    this.setActive(true);
    const finish = () => {
      this.pending = Math.max(0, this.pending - 1);
      if (this.pending === 0 && !this.buffer) this.setActive(false);
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    window.speechSynthesis.speak(utterance);
  }

  private setActive(value: boolean): void {
    if (this.active === value) return;
    this.active = value;
    for (const listener of this.listeners) listener(value);
  }
}

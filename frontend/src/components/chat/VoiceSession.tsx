"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { ChatStreamEvent, Mode } from "@/lib/api/types";
import {
  Speaker,
  VOICE_SAMPLE_RATE,
  encodeWav,
  languageTag,
  speakable,
  supportsSpeechInput,
  supportsSpeechOutput,
  trimSilence,
} from "@/lib/speech";

/**
 * Hands-free conversation.
 *
 * Silero VAD runs in the browser and hands over each utterance once the
 * speaker falls silent; it is sent as audio, transcribed on the server,
 * answered from the source, and the answer is spoken sentence by sentence
 * while it is still streaming.
 *
 * The microphone is deliberately off while the answer is being spoken, and
 * for a moment afterwards. On laptop speakers the detector otherwise hears
 * the app's own voice, sends that back as a question, and the conversation
 * talks to itself. "Interrupt by speaking" re-enables the mic during
 * playback for people on headphones.
 */

type Phase = "loading" | "listening" | "thinking" | "speaking" | "muted" | "error";

const VAD_VERSION = "0.0.31";
const ORT_VERSION = "1.22.0";
const MAX_UTTERANCE_SECONDS = 90;
const MIN_UTTERANCE_SECONDS = 0.5;
const MIN_INTERRUPTION_SECONDS = 1.0;
const RESUME_GRACE_MS = 700;
const BARGE_IN_KEY = "linkchat.voice.bargeIn";

type MicVadInstance = { start: () => void; pause: () => void; destroy?: () => void };

const STATUS: Record<Phase, string> = {
  loading: "Getting the microphone ready…",
  listening: "Listening. Just talk.",
  thinking: "Thinking…",
  speaking: "Speaking…",
  muted: "Microphone off.",
  error: "Voice is not available.",
};

export function VoiceSession({
  title,
  mode,
  sendVoice,
  stopAnswer,
  onClose,
}: {
  title: string;
  mode: Mode;
  sendVoice: (audio: Blob, onEvent: (event: ChatStreamEvent) => void) => Promise<void>;
  stopAnswer: () => void;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [heard, setHeard] = useState("");
  const [answer, setAnswer] = useState("");
  const [bargeIn, setBargeIn] = useState(false);
  const [turns, setTurns] = useState(0);

  const phaseRef = useRef<Phase>("loading");
  const bargeInRef = useRef(false);
  const vadRef = useRef<MicVadInstance | null>(null);
  const micOnRef = useRef(false);
  const speakerRef = useRef<Speaker | null>(null);
  const streamDoneRef = useRef(true);
  const closedRef = useRef(false);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechStartedWhileSpeaking = useRef(false);
  const heardRef = useRef("");

  const go = useCallback((next: Phase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  // ---- microphone on/off, the heart of the echo fix ----

  const micOn = useCallback(() => {
    if (resumeTimer.current) {
      clearTimeout(resumeTimer.current);
      resumeTimer.current = null;
    }
    const vad = vadRef.current;
    if (!vad || micOnRef.current || closedRef.current) return;
    vad.start();
    micOnRef.current = true;
  }, []);

  const micOff = useCallback(() => {
    if (resumeTimer.current) {
      clearTimeout(resumeTimer.current);
      resumeTimer.current = null;
    }
    const vad = vadRef.current;
    if (!vad || !micOnRef.current) return;
    vad.pause();
    micOnRef.current = false;
  }, []);

  /** Back to listening, after the room has gone quiet. */
  const listenAgain = useCallback(() => {
    if (closedRef.current || phaseRef.current === "muted") return;
    go("listening");
    if (micOnRef.current) return;
    resumeTimer.current = setTimeout(micOn, RESUME_GRACE_MS);
  }, [go, micOn]);

  useEffect(() => {
    bargeInRef.current = bargeIn;
    try {
      localStorage.setItem(BARGE_IN_KEY, bargeIn ? "1" : "0");
    } catch {
      /* no storage */
    }
    // Turning barge-in on mid-answer opens the mic; turning it off closes it.
    if (phaseRef.current === "speaking") {
      if (bargeIn) micOn();
      else micOff();
    }
  }, [bargeIn, micOn, micOff]);

  const interrupt = useCallback(() => {
    speakerRef.current?.stop();
    if (!streamDoneRef.current) stopAnswer();
    streamDoneRef.current = true;
    listenAgain();
  }, [listenAgain, stopAnswer]);

  // ---- one turn ----

  const handleUtterance = useCallback(
    async (raw: Float32Array) => {
      if (closedRef.current) return;
      const startedDuringAnswer = speechStartedWhileSpeaking.current;
      speechStartedWhileSpeaking.current = false;
      if (phaseRef.current === "thinking" || phaseRef.current === "muted") return;

      const samples = trimSilence(raw);
      const seconds = samples.length / VOICE_SAMPLE_RATE;
      if (phaseRef.current === "speaking" || startedDuringAnswer) {
        // Speech during playback: stop the answer. Only a clear, deliberate
        // utterance becomes the next question; anything shorter is echo or a cough.
        interrupt();
        if (seconds < MIN_INTERRUPTION_SECONDS) return;
      } else if (seconds < MIN_UTTERANCE_SECONDS) {
        return;
      }
      const clipped =
        samples.length > MAX_UTTERANCE_SECONDS * VOICE_SAMPLE_RATE
          ? samples.subarray(0, MAX_UTTERANCE_SECONDS * VOICE_SAMPLE_RATE)
          : samples;

      const speaker = speakerRef.current!;
      speaker.stop();
      setHeard("");
      heardRef.current = "";
      setAnswer("");
      setError(null);
      streamDoneRef.current = false;
      go("thinking");
      if (!bargeInRef.current) micOff();

      let spoke = false;
      await sendVoice(encodeWav(clipped), (event) => {
        if (closedRef.current) return;
        switch (event.type) {
          case "transcript":
            setHeard(event.data.text);
            heardRef.current = event.data.text;
            speaker.setLanguage(languageTag(event.data.language, event.data.text));
            break;
          case "token":
            if (phaseRef.current === "thinking") {
              go("speaking");
              if (bargeInRef.current) micOn();
            }
            setAnswer((a) => a + event.data);
            speaker.feed(event.data);
            spoke = true;
            break;
          case "done":
            streamDoneRef.current = true;
            speaker.setLanguage(languageTag(event.data.language, heardRef.current));
            speaker.flush();
            setTurns((n) => n + 1);
            break;
          case "error":
            streamDoneRef.current = true;
            setError(event.data);
            speaker.speakAll(event.data);
            break;
        }
      });
      streamDoneRef.current = true;
      if (closedRef.current) return;
      // The ref changed during the await, which TypeScript's narrowing cannot see.
      const now = phaseRef.current as Phase;
      if (!speaker.speaking && (now === "thinking" || !spoke)) listenAgain();
    },
    [go, interrupt, listenAgain, micOff, micOn, sendVoice],
  );

  const utteranceRef = useRef(handleUtterance);
  useEffect(() => {
    utteranceRef.current = handleUtterance;
  }, [handleUtterance]);

  // ---- setup and teardown ----

  useEffect(() => {
    closedRef.current = false;
    try {
      const stored = localStorage.getItem(BARGE_IN_KEY);
      if (stored === "1") setBargeIn(true);
    } catch {
      /* no storage */
    }

    const speaker = new Speaker();
    speakerRef.current = speaker;
    const unsubscribe = speaker.onChange((speaking) => {
      if (closedRef.current) return;
      if (speaking) {
        go("speaking");
        if (!bargeInRef.current) micOff();
      } else if (streamDoneRef.current && phaseRef.current === "speaking") {
        listenAgain();
      }
    });

    if (!supportsSpeechInput()) {
      setError("This browser cannot record audio here. Voice needs HTTPS and microphone access.");
      go("error");
      return () => unsubscribe();
    }
    if (!supportsSpeechOutput()) setError("This browser cannot speak answers aloud.");

    let cancelled = false;
    (async () => {
      try {
        const { MicVAD } = await import("@ricky0123/vad-web");
        const vad = await MicVAD.new({
          model: "v5",
          baseAssetPath: `https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@${VAD_VERSION}/dist/`,
          onnxWASMBasePath: `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`,
          positiveSpeechThreshold: 0.65,
          negativeSpeechThreshold: 0.45,
          redemptionMs: 900, // silence that ends a sentence
          preSpeechPadMs: 300,
          minSpeechMs: 500, // shorter blips are noise, not words
          onSpeechRealStart: () => {
            if (closedRef.current) return;
            if (phaseRef.current === "speaking") {
              speechStartedWhileSpeaking.current = true;
              speakerRef.current?.stop(); // stop talking at once; the utterance decides the rest
            }
          },
          onSpeechEnd: (audio: Float32Array) => {
            void utteranceRef.current(audio);
          },
        });
        if (cancelled) {
          vad.destroy?.();
          return;
        }
        vadRef.current = vad;
        micOn();
        go("listening");
      } catch (err) {
        if (cancelled) return;
        const name = (err as Error)?.name ?? "";
        setError(
          name === "NotAllowedError"
            ? "Microphone access was denied. Allow it in the browser's site settings and try again."
            : name === "NotFoundError"
              ? "No microphone was found."
              : "The voice detector could not start. Check your connection and try again.",
        );
        go("error");
      }
    })();

    return () => {
      cancelled = true;
      closedRef.current = true;
      if (resumeTimer.current) clearTimeout(resumeTimer.current);
      unsubscribe();
      speaker.stop();
      try {
        vadRef.current?.pause();
        vadRef.current?.destroy?.();
      } catch {
        /* already gone */
      }
      vadRef.current = null;
      micOnRef.current = false;
    };
    // The session is created once; callbacks read the latest state through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleMute() {
    if (!vadRef.current) return;
    if (phaseRef.current === "muted") {
      go("listening");
      micOn();
    } else {
      speakerRef.current?.stop();
      if (!streamDoneRef.current) stopAnswer();
      streamDoneRef.current = true;
      micOff();
      go("muted");
    }
  }

  const orb = (
    <div
      className={`relative flex h-28 w-28 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-lg ${
        phase === "listening" ? "orb-listening" : phase === "thinking" ? "orb-thinking" : ""
      }`}
      aria-hidden="true"
    >
      {phase === "speaking" ? (
        <div className="flex h-10 items-center gap-1">
          {[0, 1, 2, 3, 4].map((i) => (
            <span key={i} className="orb-bar block h-full w-1.5 rounded-full bg-white/90" />
          ))}
        </div>
      ) : phase === "muted" ? (
        <Icon name="micOff" className="h-10 w-10" />
      ) : phase === "error" ? (
        <Icon name="alert" className="h-10 w-10" />
      ) : (
        <Icon name="mic" className="h-10 w-10" />
      )}
    </div>
  );

  const status =
    error ??
    (phase === "speaking"
      ? bargeIn
        ? "Speaking. Talk over me to interrupt."
        : "Speaking. Press Stop to interrupt."
      : STATUS[phase]);

  return (
    <div
      className="flex flex-1 flex-col items-center justify-between rounded-2xl bg-white p-6 text-center ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800"
      role="region"
      aria-label="Voice conversation"
    >
      <div className="flex w-full items-start justify-between text-xs text-slate-500 dark:text-slate-400">
        <span className="truncate">
          Talking about <span className="font-medium text-slate-700 dark:text-slate-200">{title}</span>
          {mode.id !== "qa" && <> · {mode.label}</>}
        </span>
        <span>{turns > 0 ? `${turns} ${turns === 1 ? "turn" : "turns"}` : ""}</span>
      </div>

      <div className="flex flex-col items-center gap-5 py-6">
        {orb}
        <p className="text-sm font-medium" aria-live="polite">
          {status}
        </p>
        {heard && (
          <p className="max-w-md text-sm text-slate-600 dark:text-slate-300">
            <span className="text-slate-400">You: </span>
            {heard}
          </p>
        )}
        {answer && <p className="max-w-md text-sm text-slate-800 dark:text-slate-100">{speakable(answer)}</p>}
      </div>

      <div className="flex w-full flex-col items-center gap-3">
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button variant="secondary" size="sm" onClick={toggleMute} disabled={phase === "loading" || phase === "error"}>
            <Icon name={phase === "muted" ? "mic" : "micOff"} className="h-3.5 w-3.5" />
            {phase === "muted" ? "Unmute" : "Mute"}
          </Button>
          {(phase === "speaking" || phase === "thinking") && (
            <Button variant="secondary" size="sm" onClick={interrupt}>
              <Icon name="stop" className="h-3.5 w-3.5" /> Stop
            </Button>
          )}
          <Button variant="danger" size="sm" onClick={onClose}>
            <Icon name="close" className="h-3.5 w-3.5" /> End voice chat
          </Button>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <input type="checkbox" checked={bargeIn} onChange={(e) => setBargeIn(e.target.checked)} className="accent-indigo-600" />
          Interrupt by speaking (headphones only; on speakers it hears itself)
        </label>
        <p className="text-[11px] text-slate-400">
          The mic is off while the answer plays. Answers are saved to this chat with their citations. Up to{" "}
          {MAX_UTTERANCE_SECONDS} seconds per question.
        </p>
      </div>
    </div>
  );
}

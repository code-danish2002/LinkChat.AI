"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import type { ChatStreamEvent, Mode } from "@/lib/api/types";
import { Speaker, encodeWav, languageTag, speakable, supportsSpeechInput, supportsSpeechOutput } from "@/lib/speech";

/**
 * Hands-free conversation.
 *
 * Silero VAD runs in the browser and hands over each utterance once the
 * speaker falls silent; it is sent as audio, transcribed on the server,
 * answered from the source, and the answer is spoken sentence by sentence
 * while it is still streaming. Talking over the answer stops it and starts
 * a new turn, like interrupting a person.
 */

type Phase = "loading" | "listening" | "thinking" | "speaking" | "muted" | "error";

const VAD_VERSION = "0.0.31";
const ORT_VERSION = "1.22.0";
const MAX_UTTERANCE_SECONDS = 90;

type MicVadInstance = { start: () => void; pause: () => void; destroy?: () => void };

const STATUS: Record<Phase, string> = {
  loading: "Getting the microphone ready…",
  listening: "Listening. Just talk.",
  thinking: "Thinking…",
  speaking: "Speaking. Talk over me to interrupt.",
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
  const [heard, setHeard] = useState<string>("");
  const [answer, setAnswer] = useState<string>("");
  const [bargeIn, setBargeIn] = useState(true);
  const [turns, setTurns] = useState(0);

  const phaseRef = useRef<Phase>("loading");
  const bargeInRef = useRef(true);
  const vadRef = useRef<MicVadInstance | null>(null);
  const speakerRef = useRef<Speaker | null>(null);
  const streamDoneRef = useRef(true);
  const closedRef = useRef(false);

  const go = useCallback((next: Phase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  useEffect(() => {
    bargeInRef.current = bargeIn;
  }, [bargeIn]);

  const interrupt = useCallback(() => {
    speakerRef.current?.stop();
    if (!streamDoneRef.current) stopAnswer();
    streamDoneRef.current = true;
    go("listening");
  }, [go, stopAnswer]);

  const handleUtterance = useCallback(
    async (samples: Float32Array) => {
      if (closedRef.current) return;
      if (phaseRef.current === "thinking") return; // one question at a time
      if (phaseRef.current === "speaking") interrupt();
      if (phaseRef.current !== "listening") return;
      const trimmed = samples.length > MAX_UTTERANCE_SECONDS * 16000 ? samples.subarray(0, MAX_UTTERANCE_SECONDS * 16000) : samples;

      const speaker = speakerRef.current!;
      speaker.stop();
      setHeard("");
      setAnswer("");
      setError(null);
      streamDoneRef.current = false;
      go("thinking");

      let spokeSomething = false;
      await sendVoice(encodeWav(trimmed), (event) => {
        if (closedRef.current) return;
        switch (event.type) {
          case "transcript":
            setHeard(event.data.text);
            speaker.setLanguage(languageTag(event.data.language, event.data.text));
            break;
          case "token":
            if (phaseRef.current === "thinking") go("speaking");
            setAnswer((a) => a + event.data);
            speaker.feed(event.data);
            spokeSomething = true;
            break;
          case "done":
            streamDoneRef.current = true;
            speaker.setLanguage(languageTag(event.data.language, heard));
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
      // Nothing was spoken (an early error or an aborted turn): back to listening now.
      // The ref changed during the await, which TypeScript's narrowing cannot see.
      const now = phaseRef.current as Phase;
      if (!speaker.speaking && (now === "thinking" || !spokeSomething)) go("listening");
    },
    [go, heard, interrupt, sendVoice],
  );

  // Keep the latest handler reachable from VAD callbacks created once.
  const utteranceRef = useRef(handleUtterance);
  useEffect(() => {
    utteranceRef.current = handleUtterance;
  }, [handleUtterance]);

  useEffect(() => {
    closedRef.current = false;
    const speaker = new Speaker();
    speakerRef.current = speaker;
    const unsubscribe = speaker.onChange((speaking) => {
      if (closedRef.current) return;
      if (speaking) go("speaking");
      else if (streamDoneRef.current && phaseRef.current === "speaking") go("listening");
    });

    if (!supportsSpeechInput()) {
      setError("This browser cannot record audio here. Voice needs HTTPS and microphone access.");
      go("error");
      return () => {
        unsubscribe();
      };
    }
    if (!supportsSpeechOutput()) {
      setError("This browser cannot speak answers aloud.");
    }

    let cancelled = false;
    (async () => {
      try {
        const { MicVAD } = await import("@ricky0123/vad-web");
        const vad = await MicVAD.new({
          model: "v5",
          baseAssetPath: `https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@${VAD_VERSION}/dist/`,
          onnxWASMBasePath: `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`,
          positiveSpeechThreshold: 0.6,
          negativeSpeechThreshold: 0.4,
          redemptionMs: 900, // silence that ends a sentence
          preSpeechPadMs: 400,
          minSpeechMs: 350, // shorter blips are noise, not words
          onSpeechRealStart: () => {
            if (closedRef.current) return;
            if (phaseRef.current === "speaking" && bargeInRef.current) interrupt();
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
        vad.start();
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
      unsubscribe();
      speaker.stop();
      try {
        vadRef.current?.pause();
        vadRef.current?.destroy?.();
      } catch {
        /* already gone */
      }
      vadRef.current = null;
    };
    // The session is created once; callbacks read the latest state through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleMute() {
    const vad = vadRef.current;
    if (!vad) return;
    if (phaseRef.current === "muted") {
      vad.start();
      go("listening");
    } else {
      speakerRef.current?.stop();
      vad.pause();
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
          {error ?? STATUS[phase]}
        </p>
        {heard && (
          <p className="max-w-md text-sm text-slate-600 dark:text-slate-300">
            <span className="text-slate-400">You: </span>
            {heard}
          </p>
        )}
        {answer && (
          <p className="max-w-md text-sm text-slate-800 dark:text-slate-100">{speakable(answer)}</p>
        )}
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
          Interrupt by speaking (turn off if it hears its own voice through your speakers)
        </label>
        <p className="text-[11px] text-slate-400">
          Answers are still saved to this chat with their citations. Up to {MAX_UTTERANCE_SECONDS} seconds per question.
        </p>
      </div>
    </div>
  );
}

// Push-to-talk capture, local transcription, and spoken replies, as one small observable controller.
// The microphone is open only between press and release (or toggle), the state is always visible, and a mute switch
// removes the capability entirely. Audio goes to the console on this computer and nowhere else.
import { api, ApiError } from "./api";
import { concatFloat32, encodeWav, rms } from "./wav";

export type VoiceMode = "idle" | "recording" | "processing" | "speaking" | "muted";
export interface VoiceSnapshot {
  mode: VoiceMode; level: number; seconds: number; error: string | null; lastTranscript: { text: string; ms: number; model: string; speech: boolean } | null;
}

const MAX_SECONDS = 60;
let snap: VoiceSnapshot = { mode: "idle", level: 0, seconds: 0, error: null, lastTranscript: null };
const subs = new Set<() => void>();
const emit = (patch: Partial<VoiceSnapshot>) => { snap = { ...snap, ...patch }; subs.forEach((f) => f()); };

let ctx: AudioContext | null = null;
let stream: MediaStream | null = null;
let node: AudioWorkletNode | null = null;
let chunks: Float32Array[] = [];
let startedAt = 0;
let timer: number | null = null;
let audioEl: HTMLAudioElement | null = null;
let audioUrl: string | null = null;

export const voice = {
  subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f); }; },
  snapshot: () => snap,

  setMuted(m: boolean) {
    if (m) { void this.cancel(); this.stopSpeaking(); emit({ mode: "muted" }); }
    else if (snap.mode === "muted") emit({ mode: "idle" });
  },

  async start(): Promise<void> {
    if (snap.mode === "muted") { emit({ error: "The microphone is muted. Unmute it to talk." }); return; }
    if (snap.mode === "recording" || snap.mode === "processing") return;
    this.stopSpeaking();                                   // barge-in: speaking to JARVIS stops JARVIS talking
    emit({ error: null });
    if (!navigator.mediaDevices?.getUserMedia) { emit({ error: "This browser has no microphone access (it needs a secure page such as 127.0.0.1)." }); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      const name = (e as DOMException).name;
      emit({ error: name === "NotAllowedError" ? "Microphone permission was denied. Allow it in the browser's site settings, then try again."
        : name === "NotFoundError" ? "No microphone was found. Connect one, or type the command instead."
        : name === "NotReadableError" ? "The microphone is in use by another application." : `The microphone could not be opened (${name}).`, mode: "idle" });
      return;
    }
    try {
      ctx = new AudioContext({ sampleRate: 16000 });
      await ctx.audioWorklet.addModule("/pcm-worklet.js");
      const src = ctx.createMediaStreamSource(stream);
      node = new AudioWorkletNode(ctx, "pcm-capture");
      chunks = [];
      node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
        chunks.push(ev.data);
        emit({ level: Math.min(1, rms(ev.data) * 6) });
      };
      src.connect(node);
      startedAt = performance.now();
      timer = window.setInterval(() => {
        const s = (performance.now() - startedAt) / 1000;
        emit({ seconds: s });
        if (s >= MAX_SECONDS) void this.finish("en");
      }, 200);
      emit({ mode: "recording", seconds: 0, level: 0 });
    } catch {
      this.release();
      emit({ error: "The audio engine could not start.", mode: "idle" });
    }
  },

  release() {
    if (timer) { clearInterval(timer); timer = null; }
    node?.disconnect(); node = null;
    stream?.getTracks().forEach((t) => t.stop()); stream = null;   // the browser's recording indicator goes off here
    void ctx?.close(); ctx = null;
  },

  async cancel() {
    if (snap.mode !== "recording") return;
    this.release(); chunks = [];
    emit({ mode: "idle", level: 0, seconds: 0 });
  },

  /** Stop recording and transcribe. Resolves to the text (possibly empty) or null on failure; errors are in the snapshot. */
  async finish(lang: string): Promise<string | null> {
    if (snap.mode !== "recording") return null;
    const rate = ctx?.sampleRate ?? 16000;
    const audio = concatFloat32(chunks);
    this.release(); chunks = [];
    if (audio.length < rate * 0.25) { emit({ mode: "idle", level: 0, error: "That was too short. Hold the button while you speak." }); return null; }
    emit({ mode: "processing", level: 0 });
    try {
      const r = await api.audio(`/api/voice/transcribe?lang=${encodeURIComponent(lang)}`, encodeWav(audio, rate));
      emit({ mode: "idle", lastTranscript: { text: r.text, ms: r.ms, model: r.model, speech: r.speech },
        error: r.speech && r.text ? null : "I did not hear any speech. Try again, or type the command." });
      return r.text;
    } catch (e) {
      emit({ mode: "idle", error: e instanceof ApiError ? `Speech recognition failed: ${e.message}` : "Speech recognition failed." });
      return null;
    }
  },

  async speak(text: string, lang: string): Promise<void> {
    if (snap.mode === "muted" || !text.trim()) return;
    this.stopSpeaking();
    try {
      const blob = await api.speech(text, lang);
      audioUrl = URL.createObjectURL(blob);
      audioEl = new Audio(audioUrl);
      audioEl.onended = () => { this.stopSpeaking(); };
      audioEl.onerror = () => { this.stopSpeaking(); };
      emit({ mode: "speaking" });
      await audioEl.play();
    } catch (e) {
      this.stopSpeaking();
      emit({ error: e instanceof ApiError ? `I could not speak that: ${e.message}` : "I could not speak that." });
    }
  },

  stopSpeaking() {
    if (audioEl) { audioEl.pause(); audioEl.onended = null; audioEl.onerror = null; audioEl = null; }
    if (audioUrl) { URL.revokeObjectURL(audioUrl); audioUrl = null; }
    if (snap.mode === "speaking") emit({ mode: "idle" });
  },

  clearError() { emit({ error: null }); },
};

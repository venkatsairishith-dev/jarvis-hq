import { useCallback, useEffect, useRef, useState } from "react";
import { owner } from "./api";

export interface JobView { id: string; kind: string; state: string; created: number; finished: number | null; result: Record<string, unknown> | null }

/** Queue one PC action through the kernel and follow the job until it ends. The result is what the laptop node observed. */
export function usePcAction() {
  const [job, setJob] = useState<JobView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const stop = () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null; } };
  useEffect(() => stop, []);

  const run = useCallback(async (body: Record<string, unknown>) => {
    stop(); setError(null); setBusy(true); setJob(null);
    try {
      const q = await owner.post<{ job: string }>("pc", body);
      setBusy(false);
      const started = Date.now();
      const poll = async () => {
        try {
          const j = await owner.get<JobView>(`job/${q.job}`);
          setJob(j);
          if (["done", "failed", "expired"].includes(j.state) || Date.now() - started > 120_000) stop();
        } catch (e) { setError((e as Error).message); stop(); }
      };
      await poll();
      timer.current = window.setInterval(() => void poll(), 1000);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }, []);

  return { job, error, busy, run };
}

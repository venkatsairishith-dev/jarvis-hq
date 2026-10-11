import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";

// Live: a spoken request (fake microphone fed from the approved phrase "Open my downloads folder") becomes a real kernel
// command and task, its outcome is displayed, and the SAME text is what is spoken back. Latencies and process memory are
// measured on the way. English only: Hindi/Telugu need a person speaking (see Settings -> Voice check).
const getJson = async (page: Page, p: string) => (await page.request.get(p)).json();

function resident() {
  const ps = `Get-CimInstance Win32_Process | Where-Object { $_.WorkingSetSize -gt 150MB -and $_.Name -match 'python|llama|ollama|msedge' } | ForEach-Object {
    $c = $_.CommandLine; $tag = if ($c -match 'jarvis_console') {'console'} elseif ($c -match 'piper_worker') {'piper-worker'} elseif ($c -match 'llama|ollama') {'local-llm'} elseif ($c -match 'jarvis_node') {'node'} else {$_.Name}
    '{0}|{1}|{2}' -f $tag, [int]($_.WorkingSetSize/1MB), $_.ProcessId }`;
  const out = execFileSync("powershell", ["-NoProfile", "-Command", ps], { encoding: "utf8" }).trim().split(/\r?\n/).filter(Boolean);
  return out.map((l) => { const [tag, mb, pid] = l.split("|"); return { tag, mb: Number(mb), pid: Number(pid) }; });
}
function wavSeconds(b: Buffer) {
  if (b.length < 44 || b.toString("ascii", 0, 4) !== "RIFF") return 0;
  const rate = b.readUInt32LE(24), bytesPerSec = b.readUInt32LE(28);
  return bytesPerSec ? (b.length - 44) / bytesPerSec : rate ? 0 : 0;
}

test("a spoken request becomes a real kernel task and the spoken answer is the displayed answer", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  const before = resident();
  console.log("RESIDENT_BEFORE", JSON.stringify(before));
  const calls: { path: string; t: number }[] = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (/^\/api\/voice\//.test(u.pathname)) calls.push({ path: u.pathname, t: Date.now() }); });
  await page.addInitScript(() => {                                          // record what the app really receives from the speech endpoint
    const real = window.fetch.bind(window);
    (window as unknown as { __speak: unknown[] }).__speak = [];
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const res = await real(input, init);
      if (/\/api\/voice\/speak$/.test(url)) {
        const buf = new Uint8Array(await res.clone().arrayBuffer());
        (window as unknown as { __speak: unknown[] }).__speak.push({ text: JSON.parse(String(init?.body ?? "{}")).text, status: res.status, bytes: buf.length,
          riff: String.fromCharCode(...buf.slice(0, 4)), rate: new DataView(buf.buffer).getUint32(24, true), bps: new DataView(buf.buffer).getUint32(28, true) });
      }
      return res;
    };
  });
  await page.goto("/");
  await page.waitForTimeout(3000);                                         // the warm-up the UI sends on start
  await page.getByRole("button", { name: "Talk" }).click();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await page.waitForTimeout(1900);                                        // one pass of the 2.0 s clip
  const tStop = Date.now();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  const box = page.locator("#command-input");
  await expect(box).toHaveValue(/downloads folder/i, { timeout: 60_000 });
  const tTranscript = Date.now();
  const heard = await box.inputValue();
  console.log("LATENCY_STOP_TO_TRANSCRIPT_MS", tTranscript - tStop, "TRANSCRIPT", JSON.stringify(heard));

  const speakReq = page.waitForRequest((r) => /\/api\/voice\/speak$/.test(r.url()), { timeout: 8 * 60_000 });
  const tSend = Date.now();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const mine = async () => (await getJson(page, "/api/owner/commands")).commands.find((c: { text: string; ts: number }) => c.text === heard && c.ts >= tSend / 1000 - 5);   // this run's, not an earlier identical one
  await expect.poll(async () => (await mine())?.state, { timeout: 8 * 60_000, intervals: [2000] }).toMatch(/^(done|failed)$/);
  const cmd = await mine();
  const tDone = Date.now();
  console.log("COMMAND", JSON.stringify({ state: cmd.state, source: cmd.source, ms: tDone - tSend, reply: (cmd.reply ?? "").slice(0, 300) }));
  expect(cmd.state).toBe("done");
  expect(cmd.source).toMatch(/console|voice|ui/i);

  await speakReq;
  await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __speak: unknown[] }).__speak)).length, { timeout: 60_000 }).toBeGreaterThan(0);
  const got = (await page.evaluate(() => (window as unknown as { __speak: unknown[] }).__speak)) as { text: string; status: number; bytes: number; riff: string; bps: number }[];
  const sp = got[0];
  const sec = sp.bps ? (sp.bytes - 44) / sp.bps : 0;
  const tSpoken = Date.now();
  const norm = (x: string) => x.replace(/\s+/g, " ").trim();
  console.log("SPOKEN", JSON.stringify({ chars: sp.text.length, status: sp.status, wav_bytes: sp.bytes, riff: sp.riff, wav_seconds: Number(sec.toFixed(2)),
    ms_after_done: tSpoken - tDone, same_as_displayed: norm(sp.text) === norm(cmd.reply) }));
  expect(sp.status).toBe(200);
  expect(sp.riff).toBe("RIFF");
  expect(sec).toBeGreaterThan(0.3);
  expect(norm(sp.text)).toBe(norm(cmd.reply));                              // what is said is what is shown
  await expect(page.getByText(cmd.reply.slice(0, 40)).first()).toBeVisible();
  // a real task behind it: the node opened (or found) the window
  const st = await getJson(page, "/api/owner/state");
  const opened = st.jobs.filter((j: { kind: string }) => j.kind === "pc_open").slice(0, 2);
  console.log("PC_OPEN_JOBS", JSON.stringify(opened.map((j: { state: string; result: string }) => ({ state: j.state, result: String(j.result).slice(0, 160) }))));
  expect(opened.length).toBeGreaterThan(0);
  const after = resident();
  console.log("RESIDENT_AFTER", JSON.stringify(after));
  console.log("VOICE_CALLS", JSON.stringify(calls.map((c) => c.path)));
});

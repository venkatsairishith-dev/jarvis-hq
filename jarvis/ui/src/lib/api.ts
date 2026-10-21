// One thin client for the console. Every failure is an ApiError with a message the UI can show as is.
export class ApiError extends Error {
  status: number;
  unreachable: boolean;
  constructor(message: string, status: number, unreachable = false) {
    super(message);
    this.status = status;
    this.unreachable = unreachable;
  }
}

// The console's per-session double-submit token (audit SEC-D9, re-review M3 2026-10-19). The browser signs in with a one-time launch
// code (cookie, HttpOnly, never visible to this script); the signed-in page the console serves carries a token derived from that
// session in <meta name="jarvis-console-secret">, and every POST and DELETE echoes it in X-Jarvis-Console. It is NOT the console's
// master token (that never goes in a page) and a bare `GET /` without a session gets a login page with no token at all. If the
// console restarted since this page loaded the session is gone: the 403 says `reload`, the page re-reads "/" and sends the request
// once more; when that returns the login page, the error text tells the owner to open the console from JARVIS again.
const META = "jarvis-console-secret";
let secret: string | null = null;

export function consoleSecret(): string {
  if (secret === null) {
    secret = typeof document === "undefined" ? "" : (document.querySelector(`meta[name="${META}"]`)?.getAttribute("content") ?? "");
  }
  return secret;
}

export async function refreshConsoleSecret(): Promise<string> {
  try {
    const html = await (await fetch("/", { cache: "no-store" })).text();
    secret = new RegExp(`<meta name="${META}" content="([^"]*)"`).exec(html)?.[1] ?? "";
  } catch {
    secret = "";
  }
  return secret;
}

export function forgetConsoleSecret(): void {
  secret = null;
}

async function call<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown, init: RequestInit = {}, retried = false): Promise<T> {
  let res: Response;
  const raw = body instanceof Blob || body instanceof ArrayBuffer;
  try {
    res = await fetch(path, {
      method,
      headers: raw ? { "X-Jarvis-Console": consoleSecret() } : { "Content-Type": "application/json", "X-Jarvis-Console": consoleSecret() },
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
      ...init,
    });
  } catch {
    throw new ApiError("The console is not answering.", 0, true);
  }
  const ctype = res.headers.get("content-type") ?? "";
  if (ctype.includes("application/json")) {
    const data = await res.json().catch(() => ({}));
    if (res.status === 403 && (data as { reload?: boolean }).reload === true && method !== "GET" && !retried) {
      await refreshConsoleSecret();
      return call<T>(method, path, body, init, true);
    }
    if (!res.ok) throw new ApiError((data as { error?: string }).error ?? `Request failed (HTTP ${res.status})`, res.status, Boolean((data as { kernel_unreachable?: boolean }).kernel_unreachable));
    return data as T;
  }
  if (!res.ok) throw new ApiError(`Request failed (HTTP ${res.status})`, res.status);
  return (await res.blob()) as unknown as T;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body?: unknown) => call<T>("POST", path, body ?? {}),
  del: <T>(path: string) => call<T>("DELETE", path),
  audio: (path: string, body: Blob) => call<{ text: string; speech: boolean; language: string; model: string; ms: number; audio_s: number }>("POST", path, body),
  speech: (text: string, lang: string) => call<Blob>("POST", "/api/voice/speak", { text, lang }),
};

export const owner = {
  get: <T>(name: string) => api.get<T>(`/api/owner/${name}`),
  post: <T>(name: string, body?: unknown) => api.post<T>(`/api/owner/${name}`, body),
};

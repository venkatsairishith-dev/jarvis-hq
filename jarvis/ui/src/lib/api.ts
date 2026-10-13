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

async function call<T>(method: "GET" | "POST", path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body instanceof Blob || body instanceof ArrayBuffer ? { "X-Jarvis-Console": "1" } : { "Content-Type": "application/json", "X-Jarvis-Console": "1" },
      body: body === undefined ? undefined : body instanceof Blob || body instanceof ArrayBuffer ? body : JSON.stringify(body),
      ...init,
    });
  } catch {
    throw new ApiError("The console is not answering.", 0, true);
  }
  const ctype = res.headers.get("content-type") ?? "";
  if (ctype.includes("application/json")) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError((data as { error?: string }).error ?? `Request failed (HTTP ${res.status})`, res.status, Boolean((data as { kernel_unreachable?: boolean }).kernel_unreachable));
    return data as T;
  }
  if (!res.ok) throw new ApiError(`Request failed (HTTP ${res.status})`, res.status);
  return (await res.blob()) as unknown as T;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body?: unknown) => call<T>("POST", path, body ?? {}),
  audio: (path: string, body: Blob) => call<{ text: string; speech: boolean; language: string; model: string; ms: number; audio_s: number }>("POST", path, body),
  speech: (text: string, lang: string) => call<Blob>("POST", "/api/voice/speak", { text, lang }),
};

export const owner = {
  get: <T>(name: string) => api.get<T>(`/api/owner/${name}`),
  post: <T>(name: string, body?: unknown) => api.post<T>(`/api/owner/${name}`, body),
};

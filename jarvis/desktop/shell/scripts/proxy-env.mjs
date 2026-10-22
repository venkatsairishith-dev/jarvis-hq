const LOOPBACK_NO_PROXY_ENTRIES = ["127.0.0.1", "localhost", "::1"];

function splitNoProxy(value) {
  return String(value ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function ensureLoopbackNoProxy(env) {
  const entries = [
    ...splitNoProxy(env.NO_PROXY),
    ...splitNoProxy(env.no_proxy),
    ...LOOPBACK_NO_PROXY_ENTRIES,
  ];
  const seen = new Set();
  const merged = [];

  for (const entry of entries) {
    const normalized = entry.toLowerCase();
    if (seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    merged.push(entry);
  }

  const value = merged.join(",");
  env.NO_PROXY = value;
  env.no_proxy = value;
  return value;
}

"""Build the 'analyze my computer' report from collected state only. Every number printed comes from the state."""
import re

LB = "(available memory: free plus cache that can be reused at once)"


def build(node: dict) -> str:
    s, name = node["state"], node["name"]
    if not s:
        return f"No state received from {name} yet."
    L = []
    status = "online" if node["online"] else f"OFFLINE (last report {node['seconds_since_report']} s ago; data below is stale)"
    L.append(f"Node {name}: {status}. State collected at {s['collected_at']} in {s['collect_seconds']} s.")
    L.append(f"Machine: {s['host']['manufacturer']} {s['host']['model']}, {s['os']['caption']} build {s['os']['build']}, up {s['os']['uptime_hours']} hours.")
    L.append(f"CPU: {s['cpu']['name']}, {s['cpu']['cores']} cores, {s['cpu']['threads']} threads, load {s['cpu']['load_pct']} percent.")
    L.append(f"Memory: {s['ram']['total_gb']} GB total, {s['ram']['free_gb']} GB free {LB}, {s['ram']['used_pct']} percent used.")
    for g in s["gpu"]:
        L.append(f"GPU: {g['name']}, {g['vram_gb']} GB reported, driver {g['driver']}.")
    for d in s["disks"]:
        L.append(f"Disk {d['drive']} {d['size_gb']} GB, {d['free_gb']} GB free ({d['free_pct']} percent).")
    L.append("Top memory use: " + ", ".join(f"{p['name']} {p['mem_mb']} MB" for p in s["processes_top"]) + ".")
    L.append(f"Services: {s['services']['running']} running; auto-start but stopped: " + (", ".join(s["services"]["stopped_auto"]) or "none") + ".")
    L.append("Listening ports: " + ", ".join(f"{p['port']} ({p['process']})" for p in s["ports"][:15]) + ".")
    have = [f"{t} {v.get('version') or ''}".strip() for t, v in s["tools"].items() if v.get("present")]
    miss = [t for t, v in s["tools"].items() if not v.get("present")]
    L.append("Tools found: " + ("; ".join(have) or "none") + ". Not found: " + (", ".join(miss) or "none") + ".")
    # Observations come from simple thresholds on stored values.
    obs = []
    for d in s["disks"]:
        if d["free_pct"] < 15:
            obs.append(f"Disk {d['drive']} is low: {d['free_pct']} percent free.")
    if s["ram"]["used_pct"] > 85:
        obs.append(f"Memory is under pressure: {s['ram']['used_pct']} percent used.")
    if not s["tools"].get("ollama", {}).get("present"):
        obs.append("Ollama is not installed, so no local model runtime is available yet.")
    smb = [p["port"] for p in s["ports"] if p["port"] in (139, 445)]
    if smb:
        obs.append("File-sharing (SMB) ports are listening: " + ", ".join(str(p) for p in smb) + ". Confirm the network profile is Private, not Public.")
    unset = [k for k, v in s["env_paths"].items() if not v]
    if unset:
        obs.append("These cache redirects are not set: " + ", ".join(unset) + ".")
    L.append("Observations: " + (" ".join(obs) if obs else "nothing needs attention."))
    L.append("Not checked: GPU speed, model performance, and anything the collector does not read. Recommended actions need approval.")
    return "\n".join(L)


_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)*(?!\d)")  # whole dotted numbers; digits glued to letters in names are skipped


def _flatten(x, out):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        out.add(str(x)); out.add(str(float(x))); out.add(str(int(x)) if float(x).is_integer() else str(x))
    elif isinstance(x, str):
        for m in re.finditer(r"\d+(?:\.\d+)*", x):
            out.add(m.group(0))
    elif isinstance(x, dict):
        for v in x.values():
            _flatten(v, out)
    elif isinstance(x, list):
        for v in x:
            _flatten(v, out)


def unmatched_numbers(report: str, node: dict):
    """Numeric tokens in the report that do not appear anywhere in the node state or node metadata."""
    allowed = set()
    _flatten(node["state"], allowed)
    _flatten({"s": node["seconds_since_report"]}, allowed)
    return [m for m in _NUM.findall(report) if m not in allowed]

"""MEM-M5: every hardware profile must define the llama-swap aliases that JARVIS code asks for, or say in the file that it does
not. Run: python -m pytest jarvis/local_models/test_profile_aliases.py

Callers: plugin/plugins/desktop/decide.py asks for DEFAULT_MODEL ("decider"); jarvis/node/jarvis_node/vision.py asks for
VISION_MODEL ("vision-ground"). Before the fix only llama-swap.yaml (vram4) had them, so on cpu8, cpu16 and vram8plus every
PC task fell back to "no plan" and vision could never answer. remote_only starts no local models (local-models.ps1) and so
has no file.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
JARVIS = os.path.dirname(HERE)
REPO = os.path.dirname(JARVIS)
CONFIGS = {"vram4": os.path.join(HERE, "llama-swap.yaml")}
CONFIGS.update({n[:-5]: os.path.join(HERE, "profiles", n) for n in sorted(os.listdir(os.path.join(HERE, "profiles"))) if n.endswith(".yaml")})
# Profiles that deliberately lack an alias, and why (the profile's own file must say so: "(no vision on cpu8)").
DOCUMENTED_ABSENT = {"cpu8": {"vision-ground": "no vision on cpu8"}}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def caller_aliases():
    """The aliases the code asks for, read from the callers' own source so this test follows them."""
    decide = read(os.path.join(REPO, "plugin", "plugins", "desktop", "decide.py"))
    vision = read(os.path.join(JARVIS, "node", "jarvis_node", "vision.py"))
    d = re.search(r'(?m)^DEFAULT_MODEL\s*=\s*"([^"]+)"', decide)
    v = re.search(r'(?m)^VISION_MODEL\s*=\s*os\.environ\.get\("NEKO_VISION_MODEL",\s*"([^"]+)"\)', vision)
    assert d and v, "the callers' alias constants moved: update this test"
    return {"decide.DEFAULT_MODEL": d.group(1), "vision.VISION_MODEL": v.group(1)}


def uncommented(path):
    return "\n".join(ln for ln in read(path).splitlines() if not ln.lstrip().startswith("#"))


def models(path):
    """{model id: {"aliases": [...], "cmd": str}} for the models section."""
    text = uncommented(path)
    section = text.split("\nmodels:", 1)[1].split("\ngroups:", 1)[0]
    out = {}
    for block in re.split(r"(?m)^  (?=[A-Za-z0-9][\w.\-]*:\s*$)", section)[1:]:
        name = block.split(":", 1)[0]
        al = re.search(r"(?m)^\s+aliases:\s*\[([^\]]*)\]", block)
        cmd = re.search(r"(?m)^\s+cmd:\s*(.+)$", block)
        out[name] = {"aliases": [a.strip() for a in al.group(1).split(",") if a.strip()] if al else [],
                     "cmd": cmd.group(1) if cmd else ""}
    return out


def names(path):
    m = models(path)
    return set(m) | {a for v in m.values() for a in v["aliases"]}


def test_there_is_one_config_per_profile_that_starts_models():
    assert set(CONFIGS) == {"vram4", "cpu8", "cpu16", "vram8plus"}      # remote_only starts no local models (local-models.ps1)
    assert "remote_only" in read(os.path.join(JARVIS, "scripts", "local-models.ps1"))


def test_every_profile_defines_every_alias_the_callers_use_or_documents_its_absence():
    wanted = caller_aliases()
    assert wanted == {"decide.DEFAULT_MODEL": "decider", "vision.VISION_MODEL": "vision-ground"}
    for profile, path in CONFIGS.items():
        have = names(path)
        absent = DOCUMENTED_ABSENT.get(profile, {})
        for what, alias in wanted.items():
            if alias in absent:
                assert alias not in have, f"{profile}: {alias} is defined but the test says it is absent"
                assert absent[alias] in read(path), f"{profile}: the absence of {alias} ({what}) is not documented in its file"
            else:
                assert alias in have, f"{profile}: no '{alias}' model or alias ({what} asks for it)"


def test_the_decider_alias_exists_everywhere_without_exception():
    assert not any("decider" in v for v in DOCUMENTED_ABSENT.values())
    for profile, path in CONFIGS.items():
        assert any("decider" in v["aliases"] for v in models(path).values()), profile


def test_aliases_are_unique_within_a_profile_and_models_join_the_swap_group():
    for profile, path in CONFIGS.items():
        seen = {}
        for mid, v in models(path).items():
            for a in v["aliases"]:
                assert a not in seen, f"{profile}: alias {a} on both {seen[a]} and {mid}"
                seen[a] = mid
        members = set(re.findall(r"[\w.\-]+", re.search(r"members:\s*\[([^\]]*)\]", uncommented(path)).group(1)))
        for mid, v in models(path).items():
            if v["aliases"]:
                assert mid in members, f"{profile}: {mid} has aliases but is not in the swap group (it could run beside the others)"


def locked_files():
    lock = json.loads(read(os.path.join(JARVIS, "tools", "jarvis.lock.json")))
    return {os.path.basename(e["dest"]).lower() for e in lock["entries"] if e.get("dest")}


def test_the_decider_and_vision_files_are_pinned_in_the_lock():
    pinned = locked_files()
    for profile, path in CONFIGS.items():
        for mid, v in models(path).items():
            if "decider" in v["aliases"] or "vision-ground" in v["aliases"]:
                for f in re.findall(r"(?:-m|--mmproj)\s+(\S+\.gguf)", v["cmd"]):
                    assert os.path.basename(f.replace("\\", "/")).lower() in pinned, f"{profile}: {f} is not pinned in jarvis.lock.json"


def test_the_profiles_use_the_pinned_llama_cpp_build_and_a_fitting_decider():
    for profile, path in CONFIGS.items():
        assert "llama.cpp\\b11669\\" in read(path), f"{profile}: not the pinned llama.cpp build"
    dec = {p: next(m for m in models(path).values() if "decider" in m["aliases"]) for p, path in CONFIGS.items()}
    assert "Qwen3-1.7B-Q4_K_M.gguf" in dec["cpu8"]["cmd"]                      # low-memory mode: 8 GB RAM
    for p in ("vram4", "cpu16", "vram8plus"):
        assert "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" in dec[p]["cmd"], p
    assert "-ngl 99" in dec["vram8plus"]["cmd"] and "-ngl" not in dec["cpu16"]["cmd"]    # GPU offload only with a GPU


def test_vision_where_defined_uses_the_projector_settings_measured_on_the_laptop():
    for profile in ("vram4", "cpu16", "vram8plus"):
        v = next(m for m in models(CONFIGS[profile]).values() if "vision-ground" in m["aliases"])
        assert "--mmproj" in v["cmd"] and "mmproj-F16.gguf" in v["cmd"], profile      # never BF16 (RDNA1)
        assert "--no-mmproj-offload" in v["cmd"] and "--image-max-tokens 512" in v["cmd"], profile

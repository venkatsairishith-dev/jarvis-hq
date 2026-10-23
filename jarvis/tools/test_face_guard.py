"""Keeps jarvis/face/SECURITY.md true: static checks over the face source (no Node, no Electron needed).

Each test names one promise of the policy and fails if the code stops keeping it. The live checks (a real Chromium and a real
Electron) are `pnpm run check:csp` and `pnpm run check:launch` in jarvis/face/apps/stage-tamagotchi; these tests are the
cheap ones that run everywhere, including in the caretaker's test list."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FACE = Path(__file__).resolve().parents[1] / "face"
APP = FACE / "apps" / "stage-tamagotchi"
SRC = APP / "src"
SOURCE_SUFFIXES = {".ts", ".vue", ".js", ".mjs", ".json"}
SKIP_DIRS = {"node_modules", "dist", "out", ".cache", ".turbo"}


def source_files(*roots: Path):
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in SOURCE_SUFFIXES and not (set(p.parts) & SKIP_DIRS):
                yield p


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_the_policy_document_exists_and_covers_the_four_concerns():
    doc = text(FACE / "SECURITY.md")
    for heading in ("Reporting a problem", "Telemetry: none", "Malicious character cards", "Memory", "What is not covered"):
        assert heading in doc, heading
    assert "unfilled template" in doc


def test_no_analytics_or_error_reporting_library_is_a_dependency():
    banned = re.compile(r"openpanel|sentry|posthog|mixpanel|amplitude|segment|datadog|bugsnag|logrocket|hotjar|plausible|umami|google-analytics|gtag", re.I)
    for pkg in FACE.rglob("package.json"):
        if SKIP_DIRS & set(pkg.parts):
            continue
        data = json.loads(text(pkg))
        for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            for name in data.get(section, {}):
                assert not banned.search(name), f"{pkg.relative_to(FACE)}: {name}"
    catalog = text(FACE / "pnpm-workspace.yaml")
    assert not banned.search(catalog), "an analytics package is in the dependency catalog"


def test_the_analytics_adapter_cannot_be_turned_on():
    client = text(FACE / "packages/stage-ui/src/libs/product-signals/client.ts")
    assert re.search(r"isAnalyticsAvailableInBuild\(\): boolean \{\s*return false\s*\}", client)
    assert not (FACE / "packages/stage-ui/src/libs/product-signals/openpanel.ts").exists()
    assert "configureAnalyticsAdapter(async" not in text(SRC / "renderer/main.ts")


def test_the_content_policy_is_on_and_allows_no_inline_or_eval_script():
    html = text(SRC / "renderer/index.html")
    live = re.sub(r"<!--.*?-->", "", html, flags=re.S)                  # AIRI shipped it inside a comment
    m = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', live)
    assert m, "no active Content-Security-Policy meta tag"
    policy = {d.split()[0]: d.split()[1:] for d in (x.strip() for x in m.group(1).split(";")) if d}
    script = policy["script-src"]
    assert "'unsafe-inline'" not in script and "'unsafe-eval'" not in script
    assert not any(s.startswith(("http:", "https:", "*")) for s in script), "script from other sites"
    assert policy["object-src"] == ["'none'"] and policy["base-uri"] == ["'none'"] and policy["form-action"] == ["'none'"]
    assert policy["default-src"] == ["'self'"]


def test_every_raw_html_render_is_cleaned():
    allowed_static = {"scene/index.vue", "tabler-icons.ts"}              # fixed strings of the app itself (an English message, icon art)
    for p in source_files(FACE / "packages", APP / "src"):
        if p.suffix not in (".vue", ".ts") or ".test." in p.name or ".story." in p.name:
            continue
        body = text(p)
        if not re.search(r"v-html|innerHTML|outerHTML|insertAdjacentHTML|document\.write", body):
            continue
        rel = p.relative_to(FACE).as_posix()
        if any(rel.endswith(a) for a in allowed_static):
            continue
        assert "DOMPurify.sanitize(" in body, f"{rel} renders HTML without calling DOMPurify.sanitize"


def test_imported_cards_pass_the_gate():
    importer = text(FACE / "packages/stage-ui/src/services/airi-card-import-export.ts")
    assert "gateImportedCard(" in importer


def test_the_model_only_suggests_memories():
    chat = text(FACE / "packages/stage-ui/src/stores/chat.ts")
    assert "memory.propose(entry, persona)" in chat and "memory.write(entry, persona)" not in chat
    store = text(FACE / "packages/stage-ui/src/stores/memory.ts")
    assert "&& entry.proposed)" in store, "a run may withdraw only its own pending suggestions"
    assert "accepted.value.filter(entry => entry.persona === personaId)" in store, "the index must list accepted memories only"


def test_the_network_lock_is_installed_before_anything_starts():
    index = text(SRC / "main/index.ts")
    assert index.index("lockNodeSockets(") < index.index("setupDebugger()") < index.index("app.whenReady()")
    assert "applyChromiumLock(app.commandLine)" in index
    assert "installNetworkLock(session.defaultSession" in index and "lockGlobalFetch(" in index


def test_the_face_starts_no_programs_and_runs_no_computer_use():
    mcp = text(SRC / "main/services/airi/mcp-servers/index.ts")
    assert "does not start programs" in mcp and "isAllowedUrl(config.url)" in mcp
    godot = text(SRC / "main/services/airi/godot-stage/index.ts")
    assert "import { spawn } from 'node:child_process'" not in godot
    index = text(SRC / "main/index.ts")
    assert "setupComputerUse(" not in index and "setupHomeAssistant(" not in index
    assert not (SRC / "main/services/airi/computer-use").exists()
    for pkg in (APP / "package.json",):
        assert "@auv-js" not in text(pkg)


def test_no_window_turns_the_browser_protections_off():
    for p in source_files(SRC / "main"):
        body = text(p)
        assert not re.search(r"nodeIntegration\s*:\s*true", body), p
        assert not re.search(r"contextIsolation\s*:\s*false", body), p
        assert not re.search(r"webSecurity\s*:\s*false", body), p
        assert not re.search(r"allowRunningInsecureContent\s*:\s*true", body), p
        assert not re.search(r"webviewTag\s*:\s*true", body), p


def test_the_debugging_port_is_closed_in_an_installed_app():
    dbg = text(SRC / "main/app/debugger.ts")
    assert dbg.count("app.isPackaged") >= 2


def test_no_updates_and_no_release_feed():
    builder = text(APP / "electron-builder.config.ts")
    assert "publish: null" in builder
    assert "moeru-ai" not in builder and "provider: 'github'" not in builder
    assert "enabled: false" in text(SRC / "main/index.ts")
    assert not (APP / "dev-app-update.yml").exists()


def test_airi_cloud_addresses_point_at_nothing():
    for rel in ("packages/stage-ui/src/libs/server.ts", "packages/stage-ui/src/stores/inbox.ts",
                "packages/stage-ui/src/composables/cloud.ts", "apps/stage-tamagotchi/src/main/services/airi/auth.ts"):
        assert "airi.build" not in text(FACE / rel), rel
    assert "JARVIS_NO_CLOUD_URL = 'http://127.0.0.1:1'" in text(FACE / "packages/stage-shared/src/jarvis.ts")


@pytest.mark.parametrize("name", ["font-chillroundm", "font-cjkfonts-allseto", "font-xiaolai"])
def test_the_large_cjk_fonts_are_not_shipped(name):
    assert not list((FACE / "packages" / name).rglob("*.ttf")) and not list((FACE / "packages" / name).rglob("*.otf"))


def test_build_downloads_are_pinned_and_the_build_checks_them():
    lock = json.loads(text(APP / "downloads.lock.json"))
    assert len(lock["files"]) >= 5
    for path, entry in lock["files"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), path
        assert entry["from"].startswith("https://"), path
    scripts = json.loads(text(APP / "package.json"))["scripts"]
    assert "verify-downloads.mjs" in scripts["build"]
    assert "check:csp" in scripts and "check:launch" in scripts


def test_the_spine_runtime_is_not_shipped():
    assert "esotericsoftware" not in text(FACE / "pnpm-lock.yaml").lower()
    assert "esotericsoftware" not in text(FACE / "packages/stage-ui-spine/package.json").lower()

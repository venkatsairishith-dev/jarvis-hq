"""In-process IndicTrans2 distilled 200M (ONNX int8): Telugu and Hindi (script) <-> English, on this PC's CPU. No server, no network.

NOT RUN HERE. Written against the model card and the export's translate.py as read on 2026-10-26 (see docs/01_RESEARCH_AUDIT.md, "Input
translation (checked 2026-10-26)"); the laptop session installs and measures it. What it expects on D::

  D:\\jarvis\\models\\translate\\indictrans2-indic-en-dist-200M-onnx-int8\\   (hari31416/indictrans2-indic-en-dist-200M-ONNX-int8, MIT, 319.7 MB)
  D:\\jarvis\\models\\translate\\indictrans2-en-indic-dist-200M-onnx-int8\\   (hari31416/indictrans2-en-indic-dist-200M-ONNX-int8, MIT)
  each folder holds the export's own translate.py (class IndicTransONNX(model_path, providers)), which this module imports FROM THE FOLDER.
  That file is third-party code (10.7 KB): the laptop session must read it before the first run and pin the repo revision.
  pip packages in the venv that runs the gateway: onnxruntime, tokenizers, huggingface-hub, numpy, IndicTransToolkit.

Limits that are facts of the model, not of this file: greedy decoding (no beam search, per the export's translate.py), at most 128 new
tokens per call (so long text is split into sentences here), and no romanized input (IndicTrans2's card says nothing about it, so
`supports(..., romanized=True)` is False and Tenglish goes to the llama-swap backend).

Threads (D50: 3): the export creates its ONNX Runtime sessions without SessionOptions, so ONNX Runtime would use every core. `_capped()` wraps
InferenceSession for the time the folder is loaded so each session gets intra_op_num_threads = the configured threads. UNVERIFIED on the real export.
Memory (D50): the loaded models are released after `idle_unload_s` without a call (a daemon timer), so an idle PC holds none; both directions stay loaded meanwhile (`one_at_a_time: true` in the config goes back to one).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:                                                     # imported as jarvis.plugins._shared.translate_indictrans2
    from jarvis.plugins._shared.translate import Backend, TranslateError, NMT_TAGS
except ImportError:                                      # loaded by path (the server): the sibling file
    _spec = importlib.util.spec_from_file_location("jarvis_translate", Path(__file__).with_name("translate.py"))
    _mod = sys.modules.get("jarvis_translate") or importlib.util.module_from_spec(_spec)
    sys.modules["jarvis_translate"] = _mod
    if not hasattr(_mod, "Backend"):
        _spec.loader.exec_module(_mod)
    Backend, TranslateError, NMT_TAGS = _mod.Backend, _mod.TranslateError, _mod.NMT_TAGS

DEFAULT_ROOT = r"D:\jarvis\models\translate"
DIR_IN = "indictrans2-indic-en-dist-200M-onnx-int8"
DIR_OUT = "indictrans2-en-indic-dist-200M-onnx-int8"
_SENT = re.compile(r"(?<=[.!?।॥])\s+|\n+")
MAX_CHARS_PER_CALL = 220


def split_sentences(text: str, limit: int = MAX_CHARS_PER_CALL) -> list[str]:
    """Sentences, and clauses when a sentence is longer than `limit` characters (the model stops at 128 tokens)."""
    out: list[str] = []
    for s in _SENT.split(text):
        s = s.strip()
        while len(s) > limit:
            cut = max(s.rfind(",", 0, limit), s.rfind(" ", 0, limit))
            cut = cut if cut > 40 else limit
            out.append(s[:cut].strip())
            s = s[cut:].lstrip(", ")
        if s:
            out.append(s)
    return out


@contextmanager
def _capped(threads: int):
    """While active, onnxruntime.InferenceSession(...) without sess_options gets intra/inter op threads = `threads`."""
    import onnxruntime as ort
    real = ort.InferenceSession

    class Capped(real):                                   # type: ignore[misc, valid-type]
        def __init__(self, path_or_bytes, sess_options=None, *a, **k):
            if sess_options is None:
                sess_options = ort.SessionOptions()
                sess_options.intra_op_num_threads = threads
                sess_options.inter_op_num_threads = 1
            super().__init__(path_or_bytes, sess_options, *a, **k)

    ort.InferenceSession = Capped
    try:
        yield
    finally:
        ort.InferenceSession = real


class _Model:
    def __init__(self, folder: Path, threads: int):
        script = folder / "translate.py"
        spec = importlib.util.spec_from_file_location("indictrans2_onnx_export_" + folder.name.replace("-", "_"), script)
        if spec is None or spec.loader is None:
            raise TranslateError("the IndicTrans2 export folder has no translate.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with _capped(threads):
            self.m = mod.IndicTransONNX(str(folder))

    def translate(self, text: str, src_tag: str, tgt_tag: str) -> str:
        return self.m.translate(text, src_lang=src_tag, tgt_lang=tgt_tag, max_new_tokens=128)


class IndicTrans2Onnx(Backend):
    name = "indictrans2-onnx"
    confidence = 0.85            # an assumption (no per-sentence score from greedy decoding); the eval file replaces it with a measured chrF++

    def __init__(self, root: str | Path = DEFAULT_ROOT, threads: int = 3, idle_unload_s: float = 120.0, loader=None, one_at_a_time: bool = False):
        self.root, self.threads, self.idle = Path(root), int(threads), float(idle_unload_s)
        # One model at a time was the first design (D50). Measured live 2026-10-26 (3 threads): each switch of direction cost a 4.3 to 4.7 s load, and every owner turn
        # needs both directions (his words to English, the reply back), so a turn paid about 9 s of loading. Both stay loaded now (about 0.5 GB together) until idle.
        self.one_at_a_time = bool(one_at_a_time)
        self._loader = loader or (lambda folder: _Model(folder, self.threads))
        self._models: dict[str, _Model] = {}
        self._last = 0.0
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.load_seconds: dict[str, float] = {}           # measurements for the laptop session

    def _folder(self, key: str) -> Path:
        return self.root / (DIR_IN if key == "in" else DIR_OUT)

    def ready(self) -> bool:
        return any((self._folder(k) / "translate.py").exists() for k in ("in", "out"))

    def supports(self, src, tgt, romanized=False):
        if romanized:
            return False
        if src in ("te", "hi") and tgt == "en":
            return (self._folder("in") / "translate.py").exists()
        if src == "en" and tgt in ("te", "hi"):
            return (self._folder("out") / "translate.py").exists()
        return False

    def _model(self, key: str) -> _Model:
        with self._lock:
            if key not in self._models:
                if self.one_at_a_time:                        # the other direction's model is released first
                    for k in list(self._models):
                        self._models.pop(k)
                t0 = time.monotonic()
                self._models[key] = self._loader(self._folder(key))
                self.load_seconds[key] = round(time.monotonic() - t0, 2)
            return self._models[key]

    def translate(self, text, src, tgt, *, romanized=False, deadline=None):
        key = "in" if tgt == "en" else "out"
        model = self._model(key)
        self._last = time.monotonic()
        out = []
        for s in split_sentences(text):
            out.append(model.translate(s, NMT_TAGS[src], NMT_TAGS[tgt]))
            if deadline is not None and time.monotonic() > deadline + 5:     # the caller already gave up; stop burning CPU
                break
        self._arm()
        return " ".join(x.strip() for x in out if x.strip())

    def _arm(self) -> None:
        if self.idle <= 0:
            return
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle + 1, self.unload_if_idle)
        self._timer.daemon = True
        self._timer.start()

    def unload_if_idle(self) -> bool:
        if self._models and time.monotonic() - self._last >= self.idle:
            self.unload()
            return True
        return False

    def unload(self) -> None:
        with self._lock:
            self._models.clear()


def build(cfg: dict):
    """The backend for the config's "indictrans2" section, or None when neither model folder is there (then it is simply "not installed yet")."""
    sec = (cfg or {}).get("indictrans2") or {}
    b = IndicTrans2Onnx(sec.get("root") or DEFAULT_ROOT, int(sec.get("threads", 3)), float(cfg.get("idle_unload_s", 120)), one_at_a_time=bool(sec.get("one_at_a_time", False)))
    return b if b.ready() else None

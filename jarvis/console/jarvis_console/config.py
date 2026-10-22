"""Console settings. Paths and URLs only: no secret values live here (the owner token is read from its file per request)."""
import os

HQ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _env(name, default):
    return os.environ.get(name) or default


class Config:
    def __init__(self, **kw):
        self.host = "127.0.0.1"                      # never anything else: the console is the owner's own surface
        self.port = int(_env("JARVIS_CONSOLE_PORT", "8799"))
        self.kernel_url = _env("JARVIS_KERNEL_URL", "http://100.125.91.51:8790").rstrip("/")
        self.token_file = _env("JARVIS_OWNER_TOKEN_FILE", r"D:\jarvis-secrets\kernel\owner.token")
        self.hub_url = _env("JARVIS_HUB_URL", "http://hub.localhost:8088").rstrip("/")
        self.outputs_dir = _env("JARVIS_OUTPUTS_DIR", r"D:\jarvis\outputs")
        self.ui_dir = _env("JARVIS_UI_DIR", os.path.join(HQ, "ui", "dist"))
        self.voice_dir = _env("JARVIS_VOICE_DIR", r"D:\jarvis\models\voice")
        self.piper_python = _env("JARVIS_PIPER_PYTHON", r"D:\jarvis\tools\piper-venv\Scripts\python.exe")
        self.local_llm_url = _env("JARVIS_LOCAL_LLM_URL", "http://127.0.0.1:8080").rstrip("/")
        self.skills_dir = os.path.join(HQ, "skills")
        self.intent_llm_url = _env("JARVIS_INTENT_LLM_URL", self.local_llm_url)     # a small local chat model, if running
        self.intent_model = _env("JARVIS_INTENT_MODEL", "qwen3")
        self.voice_threads = int(_env("JARVIS_VOICE_THREADS", "4"))
        self.voice_idle_unload_s = int(_env("JARVIS_VOICE_IDLE_S", "300"))
        # a hub the owner (environment) or the caller (tests, launcher) named on purpose; the built-in default only exists in the lab
        self.hub_configured = bool(os.environ.get("JARVIS_HUB_URL", "").strip()) or "hub_url" in kw
        for k, v in kw.items():
            setattr(self, k, v)

    def public(self):
        """What the Settings screen may show: no token, no file contents."""
        return {"kernel_url": self.kernel_url, "hub_url": self.hub_url, "port": self.port, "host": self.host,
                "owner_token_file_present": os.path.exists(self.token_file), "outputs_dir": self.outputs_dir,
                "voice_dir": self.voice_dir, "voice_threads": self.voice_threads, "voice_idle_unload_s": self.voice_idle_unload_s,
                "local_llm_url": self.local_llm_url}

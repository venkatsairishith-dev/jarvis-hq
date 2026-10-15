"""Profile choice from fake readings (no hardware needed). Run: python -m pytest jarvis/local_models/test_hwdetect.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hwdetect as hw  # noqa: E402

LIST = """Available devices:
  Vulkan0: AMD Radeon Pro 5500M (4080 MiB, 3439 MiB free)
  CUDA0: NVIDIA GeForce RTX 3060 (12288 MiB, 11000 MiB free)
"""


def test_parse_devices():
    d = hw.parse_devices(LIST)
    assert d[0] == {"backend": "Vulkan", "name": "AMD Radeon Pro 5500M", "total_mb": 4080, "free_mb": 3439}
    assert d[1]["backend"] == "CUDA" and d[1]["free_mb"] == 11000
    assert hw.parse_devices("no devices") == []


def test_profiles():
    small_gpu = [{"backend": "Vulkan", "name": "x", "total_mb": 4080, "free_mb": 3439}]
    big_gpu = [{"backend": "CUDA", "name": "x", "total_mb": 12288, "free_mb": 11000}]
    tiny_gpu = [{"backend": "Vulkan", "name": "igpu", "total_mb": 2048, "free_mb": 1500}]
    assert hw.choose({"total_gb": 31.9}, small_gpu)[0] == "vram4"            # this laptop
    assert hw.choose({"total_gb": 32}, big_gpu)[0] == "vram8plus"
    assert hw.choose({"total_gb": 8}, [])[0] == "cpu8"
    assert hw.choose({"total_gb": 8}, tiny_gpu)[0] == "cpu8"                 # a small integrated GPU does not count
    assert hw.choose({"total_gb": 16}, [])[0] == "cpu16"
    assert hw.choose({"total_gb": 4}, big_gpu)[0] == "remote_only"


def test_override(monkeypatch):
    monkeypatch.setenv("NEKO_MODEL_PROFILE", "cpu8")
    monkeypatch.setattr(hw, "gpus", lambda runner=None: [])
    info = hw.detect()
    assert info["profile"] == "cpu8" and "NEKO_MODEL_PROFILE" in info["why"]

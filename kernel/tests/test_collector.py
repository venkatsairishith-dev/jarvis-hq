import ctypes
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
try:
    import psutil  # noqa: F401
    from jarvis_node import collector as C
    OK = os.name == "nt"
except Exception:
    OK = False

from jarvis_kernel.analyze import build, unmatched_numbers


class MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]


@unittest.skipUnless(OK, "Windows with psutil only")
class Collector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        C._TOOLS_CACHE["value"], C._TOOLS_CACHE["at"] = {t: {"present": t == "git", "version": "x"} for t in C.TOOLS}, 9e18   # no tool probing in these tests
        cls.s = C.collect()

    def test_schema_is_the_one_the_kernel_expects(self):
        s = self.s
        self.assertEqual(set(s), {"host", "os", "cpu", "ram", "gpu", "disks", "processes_top", "services", "ports", "tools", "outputs", "env_paths", "collected_at", "collect_seconds"})
        self.assertEqual(set(s["ram"]), {"total_gb", "free_gb", "used_pct"})
        self.assertTrue(s["cpu"]["name"] and s["cpu"]["threads"] >= s["cpu"]["cores"] >= 1)
        self.assertTrue(all(set(d) == {"drive", "size_gb", "free_gb", "free_pct"} for d in s["disks"]))
        self.assertTrue(all(set(p) == {"name", "mem_mb"} for p in s["processes_top"]) and len(s["processes_top"]) <= 8)
        self.assertTrue(all(set(p) == {"port", "address", "process"} for p in s["ports"]))

    def test_values_match_independent_windows_apis(self):
        m = MemStatus(); m.dwLength = ctypes.sizeof(MemStatus); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        self.assertAlmostEqual(self.s["ram"]["total_gb"], m.ullTotalPhys / 1024 ** 3, delta=0.15)
        self.assertAlmostEqual(self.s["ram"]["free_gb"], m.ullAvailPhys / 1024 ** 3, delta=2.0)     # memory moves between calls
        c = next(d for d in self.s["disks"] if d["drive"].upper().startswith("C"))
        self.assertAlmostEqual(c["free_gb"], shutil.disk_usage("C:\\").free / 1024 ** 3, delta=1.0)
        self.assertEqual(self.s["os"]["build"], str(sys.getwindowsversion().build))

    def test_core_collection_starts_no_helper_program(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("a program was started")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("a program was started")):
            C.collect()                                          # psutil and the registry only

    def test_report_is_built_only_from_these_numbers(self):
        node = {"name": "laptop", "online": True, "seconds_since_report": 1.0, "state": self.s}
        text = build(node)
        self.assertEqual(unmatched_numbers(text, node), [])
        self.assertIn(str(self.s["ram"]["total_gb"]), text)

    def test_percentages_are_consistent(self):
        r = self.s["ram"]
        self.assertAlmostEqual(r["used_pct"], 100 * (r["total_gb"] - r["free_gb"]) / r["total_gb"], delta=0.2)
        for d in self.s["disks"]:
            self.assertAlmostEqual(d["free_pct"], 100 * d["free_gb"] / d["size_gb"], delta=0.3)

    def test_tool_probe_is_cached(self):
        calls = []
        with mock.patch.object(C, "_tool", side_effect=lambda n: calls.append(n) or {"present": False}):
            C._TOOLS_CACHE["value"], C._TOOLS_CACHE["at"] = None, 0.0
            C._tools(); C._tools(); C._tools()
        self.assertEqual(len(calls), len(C.TOOLS))               # probed once, not three times


if __name__ == "__main__":
    unittest.main()

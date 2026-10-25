"""Profile "owner-compact" of the JARVIS tool server (2026-10-25): the owner's Hermes on the private lane sends fewer tokens per request.
Needs the `mcp` package (the tool server's SDK); skipped without it. Run from jarvis/lab/tests:  python -m unittest test_owner_compact_profile"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "jarvis_mcp.py")
EVALS = os.path.join(HERE, "..", "..", "evals")
try:
    import mcp.server.mcpserver  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

DROPPED = {"connector_list", "connector_call", "connector_result", "show_output", "models_status", "switch_model"}


def tools(profile):
    with tempfile.TemporaryDirectory() as home:
        open(os.path.join(home, ".jarvis-llm-token"), "w").write("made-up-test-token")
        old = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "JARVIS_MCP_PROFILE", "JARVIS_ACTIVITY")}
        os.environ.update({"HOME": home, "USERPROFILE": home, "JARVIS_MCP_PROFILE": profile, "JARVIS_ACTIVITY": os.path.join(home, "a.jsonl")})
        try:
            spec = importlib.util.spec_from_file_location("jarvis_mcp_" + profile.replace("-", "_"), MODULE)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            listed = asyncio.run(m.mcp.list_tools())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return {t.name: t.model_dump(by_alias=True, exclude_none=True) for t in listed}


@unittest.skipUnless(HAVE_MCP, "the mcp package is not installed")
class CompactProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.compact, cls.lab = tools("owner"), tools("owner-compact"), tools("lab")

    def test_it_offers_only_the_tools_an_owner_command_used(self):
        self.assertEqual(set(self.compact), {"save_output", "pc_action", "open_app", "open_folder", "open_file", "list_folder", "list_windows", "my_tasks", "task_start",
                                             "task_update", "task_get", "lab_work"})
        self.assertFalse(DROPPED & set(self.compact))

    def test_every_compact_tool_takes_the_same_arguments_as_in_the_owner_profile(self):
        for name, t in self.compact.items():
            self.assertEqual(t["inputSchema"].get("properties"), self.owner[name]["inputSchema"].get("properties"), name)
            self.assertEqual(t["inputSchema"].get("required"), self.owner[name]["inputSchema"].get("required"), name)

    def test_the_owner_and_lab_profiles_are_unchanged(self):
        self.assertTrue(DROPPED <= set(self.owner))
        self.assertEqual(len(self.owner), 19)
        self.assertIn("start_lab_task", self.lab)
        self.assertNotIn("task_start", self.lab)

    def test_the_shortened_descriptions_keep_what_keeps_the_owner_safe(self):
        lw = self.compact["lab_work"]["description"].lower()
        for needle in ("public_ok", "approval_id", "owner", "cannot see"):
            self.assertIn(needle, lw)
        tu = self.compact["task_update"]["description"].lower()
        for needle in ("proof", "never record a verification", "failed"):
            self.assertIn(needle, tu)
        self.assertLess(len(self.compact["lab_work"]["description"]), len(self.owner["lab_work"]["description"]))
        # the other tools keep the owner profile's words
        self.assertEqual(self.compact["pc_action"]["description"], self.owner["pc_action"]["description"])

    def test_the_schemas_are_smaller_and_the_replayed_requests_fit_groq(self):
        sys.path.insert(0, EVALS)
        import private_prompt_size as P
        n_o, c_o = P.tools_json("owner")
        n_c, c_c = P.tools_json("owner-compact")
        self.assertEqual((n_o, n_c), (19, 12))
        self.assertLess(c_c, c_o * 0.7)
        saved = round((c_o - c_c) * P.HERMES_FORM_RATIO)
        for label, chars, msgs, real in P.CAPTURED:
            after = chars - saved - P.UTILITY_TOOLS_CHARS
            if "c-de74" in label:
                self.assertLessEqual(P.est(after, msgs), P.LIMIT, label)        # still fits, with the utility tools off
            else:
                self.assertLess(P.est(after, msgs), 6000, label)                # a typical command: under 6000 estimated tokens


if __name__ == "__main__":
    unittest.main()

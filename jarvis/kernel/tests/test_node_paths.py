"""Where the node's workspace folder (cache\\, outputs\\) is, in the old layout and in the current one (JARVIS in <repo>\\jarvis).

A wrong answer here would silently move the folder delivered files are written to, so both layouts are pinned."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))

from jarvis_node import jobs, paths  # noqa: E402


def real(p):
    return os.path.realpath(p)


class WorkspaceFolder(unittest.TestCase):
    def test_old_layout_workspace_is_the_parent_of_hq(self):
        with tempfile.TemporaryDirectory() as ws:
            hq = os.path.join(ws, "hq")
            os.makedirs(hq)
            self.assertEqual(paths.home_for(hq), real(ws))

    def test_new_layout_workspace_is_the_parent_of_the_repo(self):
        with tempfile.TemporaryDirectory() as ws:
            repo = os.path.join(ws, "hq")
            os.makedirs(os.path.join(repo, "jarvis"))
            os.makedirs(os.path.join(repo, ".git"))                  # the repository root holds .git
            self.assertEqual(paths.home_for(os.path.join(repo, "jarvis")), real(ws))

    def test_a_folder_named_jarvis_without_the_old_layout_next_to_it_is_not_mistaken_for_the_new_layout(self):
        with tempfile.TemporaryDirectory() as ws:
            j = os.path.join(ws, "jarvis")
            os.makedirs(j)
            self.assertEqual(paths.home_for(j), real(ws))

    def test_jarvis_home_overrides_everything(self):
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as other:
            self.assertEqual(paths.home_for(ws, other), real(other))

    def test_this_checkouts_own_answer_is_outside_the_jarvis_folder_itself(self):
        old = os.environ.pop("JARVIS_HOME", None)
        try:
            home = real(paths.jarvis_home())
            subsystem = real(os.path.join(HERE, "..", ".."))
            self.assertTrue(os.path.isdir(home))
            self.assertNotEqual(home, subsystem)                                  # never the JARVIS folder itself
            self.assertFalse(home.startswith(subsystem + os.sep))                 # and never somewhere inside it
        finally:
            if old is not None:
                os.environ["JARVIS_HOME"] = old


class OutputsRoot(unittest.TestCase):
    def test_default_follows_the_workspace_and_the_explicit_override_wins(self):
        saved = {k: os.environ.pop(k, None) for k in ("JARVIS_HOME", "JARVIS_OUTPUTS")}
        try:
            with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as out:
                os.environ["JARVIS_HOME"] = ws
                self.assertEqual(jobs.outputs_root(), real(os.path.join(ws, "outputs")))
                os.environ["JARVIS_OUTPUTS"] = out
                self.assertEqual(jobs.outputs_root(), real(out))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()

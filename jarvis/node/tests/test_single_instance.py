"""One `jarvis_node run` per session (laptop 2026-10-27: Stop-ScheduledTask left the old node alive, so restarts piled up three nodes polling for the same jobs)."""
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = os.path.abspath(os.path.join(HERE, ".."))

pytestmark = pytest.mark.skipif(os.name != "nt", reason="a Windows named mutex")


def _run(code: str, **kw):
    return subprocess.Popen([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from jarvis_node import agent; %s" % (NODE, code)],
                            stdout=subprocess.PIPE, text=True, **kw)


def test_the_second_node_of_a_session_does_not_start_and_the_name_is_free_after_the_first_dies():
    name = "Local\JarvisNodeRunTest%d" % os.getpid()
    first = _run("print(agent.single_instance(%r)); sys.stdout.flush(); import time; time.sleep(30)" % name)
    try:
        assert first.stdout.readline().strip() == "True"
        second = _run("print(agent.single_instance(%r))" % name)
        assert second.communicate(timeout=30)[0].strip() == "False"
    finally:
        first.kill()
        first.wait()
    time.sleep(0.3)
    third = _run("print(agent.single_instance(%r))" % name)
    assert third.communicate(timeout=30)[0].strip() == "True"

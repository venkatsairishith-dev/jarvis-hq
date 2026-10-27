"""D77: config/exam_signals.json (what counts as a live graded or proctored test) is console-only, like ambiguity.json and own_windows.json."""
from jarvis_kernel import policy, push_gate


def test_exam_signals_is_a_safety_component_and_push_protected():
    assert "exam_signals.json" in policy.SAFETY_COMPONENTS
    assert push_gate.PROTECTED.search("jarvis/config/exam_signals.json")

"""JARVIS caretaker: deterministic health checks for every tool in jarvis/tools/registry.json and jarvis.lock.json.

detectors.py finds issues (no model calls); run.py logs them, de-duplicates them and turns new ones into kernel outcome
tasks for the lab, whose sub-agent fixes them on a branch and opens a pull request for the owner. See README.md.
"""

"""Privacy tests never write the gateway's timing log into the real data folder (D60); a test that wants it sets JARVIS_LATENCY_LOG itself."""
import os

os.environ.setdefault("JARVIS_LATENCY_LOG", "off")

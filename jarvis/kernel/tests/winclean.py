"""Temp-folder cleanup that survives Windows file locks. A background scanner or indexer can hold a freshly written git folder for
a moment, so shutil.rmtree fails with WinError 32 or 5. Retry for a few seconds; a folder that never frees up still raises."""
import os
import shutil
import stat
import time


def _writable(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def cleanup(tmp, seconds=15.0):
    """tmp: a tempfile.TemporaryDirectory. Same effect as tmp.cleanup(), with retries on PermissionError."""
    end = time.time() + seconds
    while True:
        try:
            shutil.rmtree(tmp.name, onerror=_writable)
            tmp._finalizer.detach()
            return
        except FileNotFoundError:
            tmp._finalizer.detach()
            return
        except PermissionError:
            if time.time() > end:
                raise
            time.sleep(0.5)

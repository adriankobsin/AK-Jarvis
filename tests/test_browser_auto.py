"""Auto mode: Jarvis uses your Chrome when its debug port is reachable, else his own browser."""
import os
import subprocess
import tempfile
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")


def _chromium() -> str | None:
    try:
        p = pw.sync_playwright().start()
        path = p.chromium.executable_path
        p.stop()
        return path if os.path.exists(path) else None
    except Exception:
        return None


@pytest.mark.skipif(_chromium() is None, reason="no Chromium for Playwright")
def test_auto_switches_to_connected_chrome_and_back():
    from jarvis.tools.browser import BrowserWorker

    class Cfg(dict):
        data_dir = tempfile.mkdtemp()

    wk = BrowserWorker(Cfg(browser={"mode": "auto", "cdp_url": "http://127.0.0.1:9339", "headless": True}))
    wk.call(lambda: wk.ensure())
    assert wk.kind == "own"
    ch = subprocess.Popen([_chromium(), "--headless=new", "--remote-debugging-port=9339", "--no-sandbox",
                           f"--user-data-dir={tempfile.mkdtemp()}", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            if wk.cdp_available():
                break
            time.sleep(0.25)
        wk._last_probe = 0
        wk.call(lambda: wk.ensure())
        assert wk.kind == "cdp"
    finally:
        ch.terminate()
        ch.wait()
    time.sleep(1)
    wk.call(lambda: wk.ensure())
    assert wk.kind == "own"

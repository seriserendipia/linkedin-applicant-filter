"""E2E test for the LinkedIn Applicant Filter extension.

Prereqs:
  - Xvfb stack up (~/bin/browser-up.sh)
  - Extension installed in /home/agent/.chrome-profile (tests/install_extension.py)
  - The profile is logged into LinkedIn

What this verifies (the contract):
  1. Service worker registers (extension actually loaded by Chrome).
  2. Content script injects the filter bar onto the LinkedIn jobs search page.
  3. Within ~90s, at least one job card receives a bucket badge.
  4. chrome.storage.session ends up with at least one jacf_* entry.
  5. The semantic regex is finding real applicant counts (not just 'unknown').

Run:  pytest -s tests/test_e2e.py   (-s lets you inspect via noVNC at the end)
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
EXT_DIR = "/workspace/job-applicant-count-filter"
LINKEDIN_URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278"
SCREENSHOT = "/usr/share/novnc/shots/jacf-e2e.png"


def _ext_id() -> str:
    h = hashlib.sha256(EXT_DIR.encode("utf-8")).digest()
    return "".join(chr(ord("a") + int(c, 16)) for c in h[:16].hex())


@pytest.fixture(scope="session", autouse=True)
def _require_xvfb_and_install():
    if subprocess.run(["pgrep", "-af", "Xvfb :99"]).returncode != 0:
        pytest.fail("Xvfb :99 not running. Run: ~/bin/browser-up.sh")
    # Make sure our extension is registered in the profile
    r = subprocess.run(
        ["python3", str(Path(__file__).parent / "install_extension.py")],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.fail(f"install_extension.py failed:\n{r.stdout}\n{r.stderr}")
    # Make sure no other Chrome is holding the profile lock
    subprocess.run(["/home/agent/bin/chrome-down.sh"], capture_output=True)
    time.sleep(1)


@pytest.fixture(scope="session")
def ctx():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            executable_path="/usr/bin/google-chrome",
            headless=False,
            env={**os.environ, "DISPLAY": ":99"},
            no_viewport=True,
            # Playwright defaults DISABLE extensions; we need them on.
            ignore_default_args=[
                "--disable-extensions",
                "--disable-extensions-except",
                "--disable-component-extensions-with-background-pages",
                "--disable-default-apps",
            ],
            args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--no-first-run", "--no-default-browser-check",
                "--password-store=basic",
            ],
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        # Capture service worker console too (background.js logs)
        def _wire_sw(sw):
            sw.on("console", lambda m: print(f"[sw console {m.type}] {m.text}", flush=True))
        for sw in ctx.service_workers:
            _wire_sw(sw)
        ctx.on("serviceworker", _wire_sw)
        yield ctx
        if sys.stdin.isatty() and not os.getenv("E2E_NO_PAUSE"):
            print(
                "\n>>> Chrome left running. Inspect via noVNC:"
                "\n>>>   http://localhost:6080/vnc.html?autoconnect=true&resize=scale"
                "\n>>> Press Enter to close.",
                flush=True,
            )
            try: input()
            except (EOFError, KeyboardInterrupt): pass
        ctx.close()


@pytest.fixture(scope="session", autouse=True)
def _reset_extension_storage(ctx):
    """Clear chrome.storage.{session,local} for our extension before tests so
    prior runs' UI prefs and cached counts don't leak in."""
    target_id = _ext_id()
    deadline = time.time() + 10
    sw = None
    while time.time() < deadline and sw is None:
        for s in ctx.service_workers:
            if s.url.startswith(f"chrome-extension://{target_id}/"):
                sw = s
                break
        if sw is None:
            # Trigger SW by opening any page so chrome.* APIs activate it
            p = ctx.new_page()
            p.goto("https://www.linkedin.com/", wait_until="domcontentloaded", timeout=15_000)
            p.wait_for_timeout(1500)
            p.close()
            time.sleep(0.5)
    if sw:
        sw.evaluate("""async () => {
          await chrome.storage.local.clear();
          await chrome.storage.session.clear();
        }""")


@pytest.fixture(scope="session")
def linkedin(ctx, _reset_extension_storage):
    """One LinkedIn jobs page, navigated and warmed up. Sequential tests share it."""
    page = ctx.new_page()
    page.set_viewport_size({"width": 1400, "height": 900})
    # Capture content-script console so we can diagnose failures
    page.on("console", lambda m: print(f"[page console {m.type}] {m.text}", flush=True))
    page.on("pageerror", lambda e: print(f"[page error] {e}", flush=True))
    page.goto(LINKEDIN_URL, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(6000)
    return page


# ── 1. Service worker registered (extension loaded) ────────────────────────
def test_1_service_worker_registered(ctx, linkedin):
    """
    Depends on the `linkedin` fixture so a content script has had a chance
    to send a message, which wakes our MV3 service worker (SWs are dormant
    until an event arrives).
    """
    deadline = time.time() + 15
    target_id = _ext_id()
    while time.time() < deadline:
        for sw in ctx.service_workers:
            if sw.url.startswith(f"chrome-extension://{target_id}/"):
                return
        time.sleep(0.5)
    sw_urls = [sw.url for sw in ctx.service_workers]
    pytest.fail(
        f"extension service worker for {target_id} did not register.\n"
        f"Existing service workers: {sw_urls}\n"
        f"Check /tmp/chrome.log or chrome://extensions in noVNC."
    )


# ── 2. Filter bar injected ─────────────────────────────────────────────────
def test_2_filter_bar_injected(linkedin):
    bar = linkedin.wait_for_selector("#__jacf-filter-bar", timeout=10_000)
    assert bar is not None
    # five buckets + one unknown = six checkboxes
    checks = linkedin.query_selector_all("#__jacf-filter-bar input[type=checkbox]")
    assert len(checks) == 6, f"expected 6 bucket checkboxes, got {len(checks)}"


# ── 3. At least one badge appears within 90s ───────────────────────────────
def test_3_at_least_one_badge(linkedin):
    deadline = time.time() + 90
    while time.time() < deadline:
        badges = linkedin.query_selector_all(".__jacf-badge")
        if len(badges) >= 1:
            print(f"\n→ saw {len(badges)} badge(s) after {int(time.time() - (deadline - 90))}s")
            return
        linkedin.wait_for_timeout(2000)
    pytest.fail("no .__jacf-badge appeared on any job card within 90s")


# Page-world helper: ask content script for state via the postMessage bridge.
GET_STATE_JS = r"""
() => new Promise((resolve, reject) => {
  const requestId = String(Math.random());
  const tmo = setTimeout(() => reject(new Error("get_state bridge timeout")), 3000);
  const onMsg = (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.__jacf !== "response" || msg.requestId !== requestId) return;
    clearTimeout(tmo);
    window.removeEventListener("message", onMsg);
    resolve(msg.state);
  };
  window.addEventListener("message", onMsg);
  window.postMessage({ __jacf: "request", kind: "get_state", requestId }, "*");
})
"""

def _sw_storage_session(ctx) -> dict:
    """Read chrome.storage.session directly from the SW context (it has full
    access there — content scripts have access-level restrictions)."""
    target_id = _ext_id()
    deadline = time.time() + 5
    while time.time() < deadline:
        for sw in ctx.service_workers:
            if sw.url.startswith(f"chrome-extension://{target_id}/"):
                all_ = sw.evaluate(
                    """async () => {
                      const out = await chrome.storage.session.get(null);
                      const jacf = {};
                      for (const [k, v] of Object.entries(out || {})) {
                        if (k.startsWith("jacf_")) jacf[k] = v;
                      }
                      return jacf;
                    }"""
                )
                return all_ or {}
        time.sleep(0.3)
    raise RuntimeError("no SW available to query")


# ── 4. storage.session got populated ───────────────────────────────────────
def test_4_storage_session_populated(linkedin, ctx):
    storage = _sw_storage_session(ctx)
    n = len(storage)
    print(f"\n→ storage.session has {n} jacf_* entries")
    if n > 0:
        for k, v in list(storage.items())[:3]:
            print(f"   {k} → {v}")
    assert n >= 1, "expected at least 1 jacf_ entry in chrome.storage.session"


# ── 5. At least one real (non-unknown) bucket ──────────────────────────────
def test_5_at_least_one_real_bucket(linkedin, ctx):
    data = _sw_storage_session(ctx)
    real = [v for v in data.values() if v and v.get("bucket") and v.get("bucket") != "unknown"]
    print(f"\n→ {len(real)}/{len(data)} entries have a real (non-unknown) bucket")
    if real:
        sample = real[0]
        print(f"   sample: bucket={sample.get('bucket')!r} count={sample.get('count')!r} "
              f"kind={sample.get('kind')!r} raw={sample.get('raw')!r}")
    try: linkedin.screenshot(path=SCREENSHOT, full_page=False)
    except Exception: pass
    assert len(real) >= 1, (
        "all parsed entries are 'unknown' — the regex isn't matching real "
        "LinkedIn HTML, or the fetcher is consistently failing"
    )


# ── 6. Clear button unchecks everything and reapplies filter ───────────────
def test_6_clear_button(linkedin):
    # Tick a couple of buckets first
    linkedin.evaluate(r"""() => {
      const boxes = [...document.querySelectorAll('#__jacf-filter-bar input[type=checkbox]')];
      boxes[0].click(); boxes[1].click();
    }""")
    linkedin.wait_for_timeout(300)
    checked = linkedin.evaluate("() => document.querySelectorAll('#__jacf-filter-bar input:checked').length")
    assert checked == 2, f"expected 2 checked before clear, got {checked}"

    # Click clear
    linkedin.click("#__jacf-filter-bar .__jacf-clear")
    linkedin.wait_for_timeout(200)
    checked_after = linkedin.evaluate("() => document.querySelectorAll('#__jacf-filter-bar input:checked').length")
    assert checked_after == 0, f"expected 0 checked after clear, got {checked_after}"

    state = linkedin.evaluate(GET_STATE_JS)
    assert state.get("activeBuckets") == [], f"activeBuckets should be empty: {state.get('activeBuckets')}"


# ── 7. Collapse button hides the bar; toolbar toggle restores ──────────────
def test_7_collapse_and_toolbar_toggle(linkedin, ctx):
    # Collapse via in-bar button
    linkedin.click("#__jacf-filter-bar .__jacf-collapse")
    linkedin.wait_for_timeout(200)
    hidden = linkedin.evaluate(
        "() => document.getElementById('__jacf-filter-bar').classList.contains('__jacf-hidden')"
    )
    assert hidden, "bar should have __jacf-hidden class after clicking collapse"

    # Simulate the chrome.action.onClicked path: send the toggle message via SW
    target_id = _ext_id()
    sw = next((s for s in ctx.service_workers if s.url.startswith(f"chrome-extension://{target_id}/")), None)
    assert sw is not None
    sw.evaluate(r"""async () => {
      const tabs = await chrome.tabs.query({url: "https://www.linkedin.com/jobs/*"});
      if (!tabs[0]) throw new Error("no LinkedIn tab found");
      await chrome.tabs.sendMessage(tabs[0].id, { type: "JACF_TOGGLE_BAR" });
    }""")
    linkedin.wait_for_timeout(300)
    hidden_now = linkedin.evaluate(
        "() => document.getElementById('__jacf-filter-bar').classList.contains('__jacf-hidden')"
    )
    assert not hidden_now, "toolbar toggle should have re-shown the bar"


# ── 8. Active buckets persist across navigation ────────────────────────────
def test_8_active_buckets_persist_across_navigation(linkedin):
    # Pick a known bucket and check it
    linkedin.evaluate(r"""() => {
      const cb = document.querySelector('#__jacf-filter-bar input[data-bucket="100+"]');
      if (!cb.checked) cb.click();
    }""")
    linkedin.wait_for_timeout(400)

    # Navigate to a different LinkedIn jobs search
    linkedin.goto(
        "https://www.linkedin.com/jobs/search/?keywords=product%20manager&geoId=103644278",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    linkedin.wait_for_selector("#__jacf-filter-bar", timeout=15_000)
    linkedin.wait_for_timeout(800)

    # Bucket should still be checked, and bar should NOT be collapsed
    checked = linkedin.evaluate(
        "() => document.querySelector('#__jacf-filter-bar input[data-bucket=\"100+\"]').checked"
    )
    assert checked, "100+ should remain checked after navigation (UI pref persistence)"



"""Perf benchmark — how long, on a real LinkedIn page, does the extension
take to badge every initially-visible job card?

Methodology:
  - 10 different keyword searches (varied to keep cache-hit rate low).
  - For each search: navigate, record T0 = first time the extension's filter
    bar is in DOM, then poll until every initially-visible card has a
    [data-jacf-bucket] attribute (set by paintBadgeForJob).
  - Between searches, wait for the SW's fetch queue to drain so we don't
    measure backlog.
  - Report avg / median / max + per-search detail.

This is NOT in the standard test suite — it hits LinkedIn ~70 times over
several minutes. Run explicitly:

    pytest tests/test_perf.py -v -s
"""
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
EXT_DIR = "/workspace/job-applicant-count-filter"

# Varied keywords so few cards overlap across searches → cold-cache timing.
KEYWORDS = [
    "software engineer",
    "data scientist",
    "product manager",
    "frontend developer",
    "backend developer",
    "machine learning engineer",
    "devops engineer",
    "site reliability engineer",
    "mobile developer",
    "qa engineer",
]
GEO = "103644278"  # United States


def _ext_id() -> str:
    h = hashlib.sha256(EXT_DIR.encode("utf-8")).digest()
    return "".join(chr(ord("a") + int(c, 16)) for c in h[:16].hex())


@pytest.fixture(scope="session", autouse=True)
def _require_setup():
    if subprocess.run(["pgrep", "-af", "Xvfb :99"]).returncode != 0:
        pytest.fail("Xvfb :99 not running. Run ~/bin/browser-up.sh")
    r = subprocess.run(
        ["python3", str(Path(__file__).parent / "install_extension.py")],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.fail(f"install_extension.py failed:\n{r.stdout}\n{r.stderr}")
    subprocess.run(["/home/agent/bin/chrome-down.sh"], capture_output=True)
    time.sleep(1)


def _find_sw(ctx, timeout=15):
    target_id = _ext_id()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for s in ctx.service_workers:
            if s.url.startswith(f"chrome-extension://{target_id}/"):
                return s
        time.sleep(0.3)
    raise RuntimeError("SW not found")


def _wait_queue_drained(sw, timeout=60):
    """Poll SW until its fetch queue is empty."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        stats = sw.evaluate("() => self.__jacfStats && self.__jacfStats()")
        if stats and stats.get("queueSize", 1) == 0 and not stats.get("processing"):
            return True
        time.sleep(0.5)
    return False


def test_perf_avg_load_time():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            executable_path="/usr/bin/google-chrome",
            headless=False,
            env={**os.environ, "DISPLAY": ":99"},
            no_viewport=True,
            ignore_default_args=[
                "--disable-extensions",
                "--disable-extensions-except",
                "--disable-component-extensions-with-background-pages",
                "--disable-default-apps",
            ],
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                  "--no-first-run","--no-default-browser-check","--password-store=basic"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})

        # Warm SW: navigate to a /jobs/ URL so content script injects + sends
        # the first message, which wakes up the MV3 service worker.
        page.goto(
            f"https://www.linkedin.com/jobs/search/?keywords=engineer&geoId={GEO}",
            wait_until="domcontentloaded", timeout=30_000,
        )
        page.wait_for_timeout(3000)
        sw = _find_sw(ctx)
        sw.evaluate("async () => { await chrome.storage.session.clear(); await chrome.storage.local.clear(); }")

        results = []
        for i, kw in enumerate(KEYWORDS):
            kw_url = kw.replace(" ", "%20")
            url = f"https://www.linkedin.com/jobs/search/?keywords={kw_url}&geoId={GEO}"
            print(f"\n[{i+1}/{len(KEYWORDS)}] {kw}", flush=True)

            t_nav = time.time()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)

            # Wait for our filter bar to mount → "extension visible to user"
            try:
                page.wait_for_selector("#__jacf-filter-bar", timeout=20_000, state="attached")
            except Exception:
                print("   skip: filter bar never mounted")
                continue
            t_bar = time.time()

            # Snapshot the initial set of visible cards (target = first 7,
            # which is roughly what fits in viewport before scroll)
            page.wait_for_timeout(2500)
            target = page.evaluate(r"""() => {
              const seen = new Set();
              const out = [];
              for (const el of document.querySelectorAll('[data-job-id]')) {
                const jid = el.getAttribute('data-job-id');
                if (!jid || seen.has(jid)) continue;
                seen.add(jid); out.push(jid);
                if (out.length >= 7) break;
              }
              return out;
            }""")
            if not target:
                print("   skip: no cards on page")
                continue

            # Poll until every target card has data-jacf-bucket set
            n_target = len(target)
            t_start_polling = time.time()
            deadline = t_start_polling + 90
            n_done = 0
            while time.time() < deadline:
                n_done = page.evaluate(
                    """(ids) => ids.filter(jid => {
                        const el = document.querySelector(`[data-job-id="${jid}"]`);
                        if (!el) return false;
                        const card = el.closest('[data-jacf-bucket]') || (el.hasAttribute('data-jacf-bucket') ? el : null);
                        return !!card;
                    }).length""",
                    target,
                )
                if n_done >= n_target:
                    break
                time.sleep(0.5)
            t_done = time.time()

            stats = sw.evaluate("() => self.__jacfStats && self.__jacfStats()")
            row = {
                "kw": kw,
                "n_target": n_target,
                "n_done": n_done,
                "all_done": n_done >= n_target,
                "t_nav_to_bar":   round(t_bar - t_nav, 2),
                "t_bar_to_done":  round(t_done - t_start_polling, 2),
                "t_total":        round(t_done - t_nav, 2),
                "sw_processed":   stats and stats.get("processedCount"),
                "sw_cache_size":  stats and stats.get("cacheSize"),
            }
            results.append(row)
            print(f"   bar in {row['t_nav_to_bar']}s · {n_done}/{n_target} cards badged in {row['t_bar_to_done']}s · total {row['t_total']}s")

            # Drain queue before next search so timing isn't polluted
            _wait_queue_drained(sw, timeout=60)

        ctx.close()

    if not results:
        pytest.fail("no successful iterations")

    completed = [r for r in results if r["all_done"]]
    print("\n" + "═" * 64)
    print(f"  PERF RESULTS  (n={len(results)}, fully completed: {len(completed)}/{len(results)})")
    print("═" * 64)
    print(f"  {'keyword':<28} {'n_done':>7} {'bar':>7} {'badges':>8} {'total':>8}")
    for r in results:
        marker = "✓" if r["all_done"] else "·"
        print(f"  {marker} {r['kw']:<26} {r['n_done']}/{r['n_target']:<5} {r['t_nav_to_bar']:>6}s {r['t_bar_to_done']:>7}s {r['t_total']:>7}s")

    bar_times   = [r["t_nav_to_bar"]  for r in completed]
    badge_times = [r["t_bar_to_done"] for r in completed]
    total_times = [r["t_total"]       for r in completed]

    def stats_line(name, xs):
        if not xs: return f"  {name}: no data"
        return (f"  {name:<14} avg={statistics.mean(xs):.1f}s  "
                f"median={statistics.median(xs):.1f}s  "
                f"min={min(xs):.1f}s  max={max(xs):.1f}s")

    print()
    print(stats_line("nav→bar:",   bar_times))
    print(stats_line("bar→badges:", badge_times))
    print(stats_line("nav→done:",   total_times))

    Path("/tmp/jacf_perf.json").write_text(json.dumps(results, indent=2))
    print("\n  raw data: /tmp/jacf_perf.json")

    # Soft assertion: ≥70% of searches fully complete within 90s, and avg
    # nav→done < 60s (7 cards × 2.5s jitter ≈ 17s ideal; double for noise)
    assert len(completed) >= max(1, int(0.7 * len(results))), (
        f"only {len(completed)}/{len(results)} searches fully badged"
    )
    if total_times:
        avg = statistics.mean(total_times)
        assert avg < 60, f"avg total time {avg:.1f}s is too high"

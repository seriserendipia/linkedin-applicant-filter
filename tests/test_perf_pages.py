"""Perf across pagination: do non-Promoted-heavy pages (2, 3) actually use
the fetch queue, and what's the timing?

LinkedIn pages via &start=N (25 results per page):
  page 1 → start=0
  page 2 → start=25
  page 3 → start=50

For each (keyword × page), we measure:
  - nav→bar (LinkedIn SPA paint)
  - bar→badges (time until 7 visible cards are tagged)
  - source split: how many badges came from Ember shortcut vs from HTTP fetch

The hypothesis: page 1 is mostly Promoted (Ember shortcut → ~0s). Pages 2-3
should have far fewer Promoted, so fetch-queue latency dominates.
"""
import hashlib, json, os, statistics, subprocess, time
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
EXT_DIR = "/workspace/job-applicant-count-filter"
GEO = "103644278"  # United States
KEYWORDS = ["software engineer", "data scientist", "product manager"]
PAGES = [(1, 0), (2, 25), (3, 50)]


def _ext_id():
    h = hashlib.sha256(EXT_DIR.encode("utf-8")).digest()
    return "".join(chr(ord("a") + int(c, 16)) for c in h[:16].hex())


@pytest.fixture(scope="session", autouse=True)
def _require_setup():
    if subprocess.run(["pgrep", "-af", "Xvfb :99"]).returncode != 0:
        pytest.fail("Xvfb :99 not running. Run ~/bin/browser-up.sh")
    r = subprocess.run(["python3", str(Path(__file__).parent / "install_extension.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.fail(f"install failed:\n{r.stdout}\n{r.stderr}")
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
    deadline = time.time() + timeout
    while time.time() < deadline:
        stats = sw.evaluate("() => self.__jacfStats && self.__jacfStats()")
        if stats and stats.get("queueSize", 1) == 0 and not stats.get("processing"):
            return True
        time.sleep(0.5)
    return False


def _source_split(sw, jids):
    """Read storage.session for the given jids, return {ember, fetch, other, missing}."""
    return sw.evaluate(
        """async (jids) => {
          const out = { ember: 0, fetch: 0, other: 0, missing: 0 };
          const all = await chrome.storage.session.get(null);
          for (const jid of jids) {
            const v = all['jacf_' + jid];
            if (!v) { out.missing++; continue; }
            const s = v.source;
            if (s === 'ember') out.ember++;
            else if (s === 'fetch') out.fetch++;
            else out.other++;
          }
          return out;
        }""",
        jids,
    )


def test_perf_across_pages():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, executable_path="/usr/bin/google-chrome",
            headless=False, env={**os.environ, "DISPLAY": ":99"}, no_viewport=True,
            ignore_default_args=[
                "--disable-extensions", "--disable-extensions-except",
                "--disable-component-extensions-with-background-pages",
                "--disable-default-apps",
            ],
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                  "--no-first-run","--no-default-browser-check","--password-store=basic"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})

        # Warm SW
        page.goto(
            f"https://www.linkedin.com/jobs/search/?keywords=engineer&geoId={GEO}",
            wait_until="domcontentloaded", timeout=30_000,
        )
        page.wait_for_timeout(3000)
        sw = _find_sw(ctx)

        results = []
        for kw in KEYWORDS:
            # Reset SW caches per keyword so we measure cold (no cross-keyword cache hits)
            sw.evaluate("async () => { await chrome.storage.session.clear(); await chrome.storage.local.clear(); }")

            for page_num, start in PAGES:
                kw_url = kw.replace(" ", "%20")
                url = f"https://www.linkedin.com/jobs/search/?keywords={kw_url}&geoId={GEO}"
                if start > 0:
                    url += f"&start={start}"

                t_nav = time.time()
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:
                    page.wait_for_selector("#__jacf-filter-bar", timeout=20_000, state="attached")
                except Exception:
                    print(f"\n[{kw} p{page_num}] bar never mounted, skipping")
                    continue
                t_bar = time.time()
                page.wait_for_timeout(2500)

                target = page.evaluate(r"""() => {
                  const seen = new Set(); const out = [];
                  for (const el of document.querySelectorAll('[data-job-id]')) {
                    const jid = el.getAttribute('data-job-id');
                    if (!jid || seen.has(jid)) continue;
                    seen.add(jid); out.push(jid);
                    if (out.length >= 7) break;
                  }
                  return out;
                }""")
                if not target:
                    continue

                # Wait for all 7 to be badged
                t_poll_start = time.time()
                n_done = 0
                while time.time() - t_poll_start < 60:
                    n_done = page.evaluate(
                        """(ids) => ids.filter(jid => {
                            const el = document.querySelector(`[data-job-id="${jid}"]`);
                            if (!el) return false;
                            const card = el.closest('[data-jacf-bucket]') || (el.hasAttribute('data-jacf-bucket') ? el : null);
                            return !!card;
                        }).length""",
                        target,
                    )
                    if n_done >= len(target):
                        break
                    time.sleep(0.5)
                t_done = time.time()

                split = _source_split(sw, target)
                row = {
                    "kw": kw, "page": page_num, "n_target": len(target), "n_done": n_done,
                    "all_done": n_done >= len(target),
                    "t_nav_to_bar":  round(t_bar - t_nav, 2),
                    "t_bar_to_done": round(t_done - t_poll_start, 2),
                    "t_total":       round(t_done - t_nav, 2),
                    "source_split":  split,
                }
                results.append(row)
                print(
                    f"\n[{kw} p{page_num}]  bar {row['t_nav_to_bar']}s · "
                    f"{n_done}/{len(target)} in {row['t_bar_to_done']}s · total {row['t_total']}s · "
                    f"ember={split['ember']} fetch={split['fetch']} miss={split['missing']}"
                )
                _wait_queue_drained(sw, timeout=45)

        ctx.close()

    if not results:
        pytest.fail("no successful iterations")

    # Per-page aggregation
    print("\n" + "═" * 80)
    print(f"  PER-PAGE BREAKDOWN")
    print("═" * 80)
    for page_num, _ in PAGES:
        rows = [r for r in results if r["page"] == page_num]
        if not rows: continue
        totals = [r["t_total"] for r in rows if r["all_done"]]
        bars = [r["t_nav_to_bar"] for r in rows if r["all_done"]]
        badges = [r["t_bar_to_done"] for r in rows if r["all_done"]]
        ember = sum(r["source_split"]["ember"] for r in rows)
        fetch = sum(r["source_split"]["fetch"] for r in rows)
        missing = sum(r["source_split"]["missing"] for r in rows)
        total_cards = ember + fetch + missing
        print(
            f"\n  page {page_num}  (n={len(rows)})"
            f"\n    nav→bar     avg={statistics.mean(bars):.2f}s   median={statistics.median(bars):.2f}s"
            f"\n    bar→badges  avg={statistics.mean(badges):.2f}s   median={statistics.median(badges):.2f}s"
            f"\n    nav→done    avg={statistics.mean(totals):.2f}s   median={statistics.median(totals):.2f}s"
            f"\n    sources     ember={ember}/{total_cards} ({100*ember/max(1,total_cards):.0f}%)  "
            f"fetch={fetch}/{total_cards} ({100*fetch/max(1,total_cards):.0f}%)  missing={missing}"
        )

    Path("/tmp/jacf_perf_pages.json").write_text(json.dumps(results, indent=2))
    print("\n  raw data: /tmp/jacf_perf_pages.json")

    # Sanity: all rows finish
    assert all(r["all_done"] for r in results), "some pages didn't fully badge in 60s"

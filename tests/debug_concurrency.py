"""Debug: poll SW state every 1s for 30s on a fresh LinkedIn search, log:
  - activeWorkers (is concurrency actually >1?)
  - queueSize over time (is queue draining at the right rate?)
  - cache source distribution (ember vs fetch — is the shortcut helping?)
"""
import hashlib, json, os, subprocess, time
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
EXT_DIR = "/workspace/job-applicant-count-filter"
URL = "https://www.linkedin.com/jobs/search/?keywords=python%20developer&geoId=103644278"


def _ext_id():
    h = hashlib.sha256(EXT_DIR.encode("utf-8")).digest()
    return "".join(chr(ord("a") + int(c, 16)) for c in h[:16].hex())


subprocess.run(["python3", "tests/install_extension.py"], check=True, capture_output=True)
subprocess.run(["/home/agent/bin/chrome-down.sh"], capture_output=True); time.sleep(1)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE,
        executable_path="/usr/bin/google-chrome",
        headless=False,
        env={**os.environ, "DISPLAY": ":99"},
        no_viewport=True,
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
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(3000)

    # Find SW
    target_id = _ext_id()
    sw = None
    for _ in range(20):
        for s in ctx.service_workers:
            if s.url.startswith(f"chrome-extension://{target_id}/"):
                sw = s; break
        if sw: break
        time.sleep(0.5)
    if not sw:
        print("NO SW FOUND"); ctx.close(); raise SystemExit

    # Clear caches so we measure cold start
    sw.evaluate("async () => { await chrome.storage.session.clear(); await chrome.storage.local.clear(); }")

    # Re-navigate with cleared state
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)

    print(f"{'t':>4} {'qsize':>6} {'workers':>8} {'cached':>6} {'mode':>8}  source distribution")
    print("-" * 90)
    t0 = time.time()
    for _ in range(30):
        t = time.time() - t0
        stats = sw.evaluate("() => (typeof self !== 'undefined' && self.__jacfStats) ? self.__jacfStats() : null")
        if stats is None:
            print(f"{t:>4.1f}  stats=None (SW probe failed)")
            time.sleep(1); continue
        # Get source distribution from storage.session
        srcs = sw.evaluate("""async () => {
          const all = await chrome.storage.session.get(null);
          const out = { ember: 0, fetch: 0, other: 0 };
          for (const [k, v] of Object.entries(all || {})) {
            if (!k.startsWith('jacf_')) continue;
            const s = v && v.source;
            if (s === 'ember') out.ember++;
            else if (s === 'fetch') out.fetch++;
            else out.other++;
          }
          return out;
        }""")
        print(f"{t:>4.1f} {stats['queueSize']:>6} {stats['activeWorkers']:>8} "
              f"{stats['cacheSize']:>6} {stats['mode']:>8}  "
              f"ember={srcs['ember']} fetch={srcs['fetch']} other={srcs['other']}")
        time.sleep(1)

    ctx.close()

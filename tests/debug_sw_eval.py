"""See what's actually in the SW context."""
import hashlib, os, subprocess, time
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
EXT_DIR = "/workspace/job-applicant-count-filter"
URL = "https://www.linkedin.com/jobs/search/?keywords=python&geoId=103644278"


def _ext_id():
    h = hashlib.sha256(EXT_DIR.encode("utf-8")).digest()
    return "".join(chr(ord("a") + int(c, 16)) for c in h[:16].hex())


subprocess.run(["/home/agent/bin/chrome-down.sh"], capture_output=True); time.sleep(1)
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
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(4000)

    target_id = _ext_id()
    sw = None
    for _ in range(20):
        for s in ctx.service_workers:
            if s.url.startswith(f"chrome-extension://{target_id}/"):
                sw = s; break
        if sw: break
        time.sleep(0.5)
    print("sw url:", sw.url if sw else None)

    # Probe a bunch of things
    r = sw.evaluate("""() => ({
      url: location.href,
      hasOnSelf: typeof self.__jacfStats,
      hasOnGlobal: typeof globalThis.__jacfStats,
      activeWorkersDeclared: typeof activeWorkers,
      queueDeclared: typeof queue,
      modeDeclared: typeof mode,
    })""")
    print("probe:", r)

    # Try without self prefix
    r2 = sw.evaluate("() => typeof __jacfStats")
    print("typeof __jacfStats:", r2)

    # Try assigning + reading
    sw.evaluate("() => { self.__test_marker = 42; }")
    r3 = sw.evaluate("() => self.__test_marker")
    print("test marker:", r3)

    # Read storage directly
    r4 = sw.evaluate("async () => { const all = await chrome.storage.session.get(null); return { keys: Object.keys(all).length, sample: Object.entries(all).slice(0,2) }; }")
    print("storage:", r4)

    ctx.close()

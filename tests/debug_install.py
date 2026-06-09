"""Debug: launch Chrome, see what extensions are registered."""
import json, os, time
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE,
        executable_path="/usr/bin/google-chrome",
        headless=False,
        env={**os.environ, "DISPLAY": ":99"},
        no_viewport=True,
        args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
              "--no-first-run","--no-default-browser-check","--password-store=basic"],
    )
    time.sleep(4)  # let extensions register
    print(f"\nservice_workers after 4s: {len(ctx.service_workers)}")
    for sw in ctx.service_workers:
        print(f"  {sw.url}")

    # Open a chrome:// page to inspect
    page = ctx.new_page()
    page.goto("chrome://extensions/", wait_until="domcontentloaded", timeout=15_000)
    time.sleep(3)
    # Pierce shadow DOM to get extension card info
    info = page.evaluate(r"""() => {
      const out = [];
      const manager = document.querySelector("extensions-manager");
      if (!manager) return ["no manager"];
      const list = manager.shadowRoot?.querySelector("extensions-item-list");
      if (!list) return ["no item-list"];
      const items = list.shadowRoot?.querySelectorAll("extensions-item");
      if (!items) return ["no items"];
      for (const it of items) {
        const sr = it.shadowRoot;
        const name = sr?.querySelector("#name")?.innerText;
        const id   = sr?.querySelector("#extension-id")?.innerText;
        const enabled = sr?.querySelector("#enableToggle")?.getAttribute("aria-pressed");
        const err = sr?.querySelector("#errorsLink")?.innerText;
        out.push({ name, id, enabled, err });
      }
      return out;
    }""")
    print(f"\nchrome://extensions reports:")
    for e in info:
        print(f"  {e}")
    page.screenshot(path="/usr/share/novnc/shots/install_debug.png")
    ctx.close()

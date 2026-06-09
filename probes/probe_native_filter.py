"""Activate LinkedIn's native 'under 10 applicants' filter and observe:
  1. What URL parameter encodes it
  2. Whether the cache now exposes per-job applicant numbers
  3. The exact tertiary text format on the filtered results
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
START_URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278"

SCAN_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;

  const cards = [];
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    const t = d && d.tertiaryDescription && d.tertiaryDescription.text;
    if (t) cards.push({ key: k, t });
  }

  // Also: every cache value that contains a number followed by 'applicant'
  const numericHits = [];
  for (const k of Object.keys(cache)) {
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { continue; }
    const m = s.match(/.{0,40}(\d+|over \d+|less than \d+)\s+applicant.{0,40}/gi);
    if (m) numericHits.push({ key: k, hits: m.slice(0, 3) });
    if (numericHits.length >= 8) break;
  }
  return { cards: cards.slice(0, 30), numericHits, totalCardsCached: cards.length };
}
"""


def main():
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
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(START_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(6000)

        # Try clicking the "All filters" button and toggling the under-10 filter
        # — but first try the URL-param shortcut. The LinkedIn URL param for
        # "Has less than 10 applicants" is suspected to be one of:
        #   f_EA=true (Easy Apply — not it)
        #   f_F=...
        # Let me try a known one: f_JIYN doesn't exist. The right one is via
        # LinkedIn's all-filters UI. We do that by clicking.
        try:
            page.get_by_role("button", name=re.compile("All filters", re.I)).click(timeout=4000)
            page.wait_for_timeout(1500)
            print("→ opened All filters")
        except Exception as e:
            print(f"could not open All filters: {e}")

        # Find and click the "Under 10 applicants" toggle
        clicked = False
        for label in ["Under 10 applicants", "Less than 10 applicants", "Has under 10 applicants"]:
            try:
                page.get_by_text(label, exact=False).first.click(timeout=3000)
                clicked = True
                print(f"→ toggled '{label}'")
                break
            except Exception:
                pass
        if not clicked:
            # Snapshot what's in the dialog
            try:
                html = page.locator("[role=dialog]").first.inner_text()
                print("--- dialog text dump ---")
                print(html[:2000])
            except Exception as e:
                print(f"dialog scrape failed: {e}")

        # Show results
        try:
            page.get_by_role("button", name=re.compile("Show .* results", re.I)).click(timeout=3000)
            print("→ clicked Show results")
        except Exception as e:
            print(f"could not click Show results: {e}")

        page.wait_for_timeout(5000)
        print(f"URL after filtering: {page.url}")

        scan = page.evaluate(SCAN_JS)
        print(f"\n--- cards cached ({scan['totalCardsCached']}) ---")
        for c in scan["cards"][:15]:
            jid = re.search(r"\(([^,]+),", c["key"]).group(1)
            print(f"  {jid}  | {c['t'][:100]!r}")
        print(f"\n--- numeric applicant hits ---")
        for h in scan["numericHits"]:
            print(f"  {h['key']}")
            for s in h["hits"]:
                print(f"    {s!r}")

        with open("/tmp/native_filter.json", "w") as f:
            json.dump({"url": page.url, "scan": scan}, f, indent=2, ensure_ascii=False)
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

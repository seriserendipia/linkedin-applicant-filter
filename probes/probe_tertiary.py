"""Scan tertiaryDescription for every cached jobPostingCard, before and after
scrolling+clicking through the list, to see how many we can extract without
the user touching anything."""
import json, os, re, time
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20engineer%20intern&geoId=103644278"

SCAN_JS = r"""
() => {
  const w = window;
  const Ember = w.requireModule ? w.requireModule("ember").default : w.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;

  const results = [];
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    const m = k.match(/\(([^,]+),/);
    const jobId = m ? m[1] : null;
    const tertiary = d.tertiaryDescription && d.tertiaryDescription.text;
    results.push({ jobId, tertiary });
  }
  return results;
}
"""

# Match: "1 applicant" "47 applicants" "Over 100 applicants" "100+ applicants"
APPLICANT_RE = re.compile(r"(over\s+\d+|\d+\+?|less than \d+)\s+applicant", re.I)


def summarize(results, tag):
    have = [r for r in results if r["tertiary"] and APPLICANT_RE.search(r["tertiary"])]
    print(f"[{tag}] cached cards={len(results)}  with-applicant-text={len(have)}")
    for r in have[:5]:
        m = APPLICANT_RE.search(r["tertiary"])
        print(f"  {r['jobId']}  →  {m.group(0)!r}   (full: {r['tertiary'][:90]!r})")


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            executable_path="/usr/bin/google-chrome",
            headless=False,
            env={**os.environ, "DISPLAY": ":99"},
            no_viewport=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-first-run", "--no-default-browser-check", "--password-store=basic"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        print(f"→ navigating to {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(6000)

        r0 = page.evaluate(SCAN_JS)
        summarize(r0, "after-initial-load")

        # Scroll the job list once
        print("→ scrolling job list panel")
        page.evaluate("""() => {
          const list = document.querySelector(
            '.scaffold-layout__list > div, .scaffold-layout__list > ul'
          );
          if (list) list.scrollBy(0, 4000);
        }""")
        page.wait_for_timeout(3000)
        r1 = page.evaluate(SCAN_JS)
        summarize(r1, "after-scroll")

        # Click on the first 3 job cards to force JOB_DETAILS hydration
        print("→ clicking through first 3 job cards")
        cards = page.query_selector_all(
            ".job-card-container--clickable, .job-card-job-posting-card-wrapper"
        )
        for i, c in enumerate(cards[:3]):
            try:
                c.click(timeout=3000)
                page.wait_for_timeout(1500)
                print(f"   clicked card {i}")
            except Exception as e:
                print(f"   card {i} click failed: {e}")

        r2 = page.evaluate(SCAN_JS)
        summarize(r2, "after-clicks")

        with open("/tmp/tertiary_scan.json", "w") as f:
            json.dump({"r0": r0, "r1": r1, "r2": r2}, f, indent=2, ensure_ascii=False)
        print("→ full data: /tmp/tertiary_scan.json")
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

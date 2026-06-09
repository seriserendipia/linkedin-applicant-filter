"""Drill into one fsd_jobPosting entry that the broad scan said mentions
'applicant' — dump everything to a file so we can find the actual field."""
import json, os
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20engineer%20intern&geoId=103644278"

JS = r"""
() => {
  const w = window;
  const Ember = w.requireModule ? w.requireModule("ember").default : w.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;

  // Find every fsd_jobPosting whose payload mentions 'applicant'
  const hits = {};
  const cardHits = {};
  for (const k of Object.keys(cache)) {
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { continue; }
    if (!/applicant/i.test(s)) continue;
    if (k.includes("fsd_jobPosting:") && Object.keys(hits).length < 3) hits[k] = d;
    if (k.includes("fsd_jobPostingCard:") && Object.keys(cardHits).length < 3) cardHits[k] = d;
  }
  return { hits, cardHits };
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
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-first-run", "--no-default-browser-check", "--password-store=basic"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(6000)
        res = page.evaluate(JS)
        with open("/tmp/jobposting_dump.json", "w") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
        print(f"hits: fsd_jobPosting={len(res['hits'])}  fsd_jobPostingCard={len(res['cardHits'])}")
        print("written: /tmp/jobposting_dump.json")
        ctx.close()


if __name__ == "__main__":
    main()

"""After clicking a job card, find WHERE the applicant info ends up."""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20engineer%20intern&geoId=103644278"

CACHE_KEYS_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  return Object.keys(cache);
}
"""

# Pick a job whose currently cached tertiaryDescription has NO applicant text
PICK_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    const t = d && d.tertiaryDescription && d.tertiaryDescription.text;
    if (!t || /applicant/i.test(t)) continue;
    const m = k.match(/\(([^,]+),/);
    return { jobId: m && m[1], tertiary: t };
  }
  return null;
}
"""

INSPECT_JS = r"""
(jobId) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;

  const entries = [];
  for (const k of Object.keys(cache)) {
    if (!k.includes(jobId)) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { s = String(d); }
    const hasApp = /applicant/i.test(s);
    let sample = null;
    if (hasApp) {
      const m = s.match(/.{0,80}applicant.{0,80}/i);
      sample = m && m[0];
    }
    entries.push({ key: k, hasApplicant: hasApp, size: s.length, sample });
  }
  const domHits = [];
  document.querySelectorAll("span, div, p, li").forEach(el => {
    const t = el.innerText;
    if (t && /\b(\d+\+?|over \d+|less than \d+)\s+applicant/i.test(t) && t.length < 200) {
      domHits.push(t.trim());
    }
  });
  return { entries, domHits: [...new Set(domHits)].slice(0, 10) };
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
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(6000)

        before = set(page.evaluate(CACHE_KEYS_JS))
        print(f"keys before: {len(before)}")

        pick = page.evaluate(PICK_JS)
        if not pick:
            print("no non-applicant card found in cache — aborting")
            ctx.close(); return
        target_id = pick["jobId"]
        print(f"target: {target_id}")
        print(f"  current tertiary: {pick['tertiary'][:90]!r}")

        # Click the card by data-job-id (anywhere in DOM is fine)
        clicked = page.evaluate(
            r"""(jobId) => {
              const el = document.querySelector(
                `[data-job-id="${jobId}"] .job-card-container--clickable,
                 [data-job-id="${jobId}"] .job-card-job-posting-card-wrapper,
                 [data-job-id="${jobId}"] a.job-card-container__link,
                 [data-job-id="${jobId}"]`);
              if (!el) return false;
              el.scrollIntoView({block:"center"});
              el.click();
              return true;
            }""", target_id)
        print(f"clicked? {clicked}")
        page.wait_for_timeout(4500)

        after = set(page.evaluate(CACHE_KEYS_JS))
        new_keys = sorted(after - before)
        print(f"keys after: {len(after)}  (new: {len(new_keys)})")
        for k in new_keys[:25]:
            print(" ", k)

        ins = page.evaluate(INSPECT_JS, target_id)
        print(f"\n--- entries for {target_id} ---")
        for e in ins["entries"]:
            print(f"  app={e['hasApplicant']:1}  sz={e['size']:6}  {e['key']}")
            if e["sample"]:
                print(f"      {e['sample']!r}")
        print(f"\n--- DOM hits ({len(ins['domHits'])}) ---")
        for h in ins["domHits"]:
            print(f"  {h!r}")

        with open("/tmp/click_dump.json", "w") as f:
            json.dump({"target_id": target_id, "new_keys": new_keys,
                       "inspection": ins, "picked": pick}, f, indent=2, ensure_ascii=False)
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

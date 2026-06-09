"""v6 — definitive: find WHERE 'Over 100 people clicked apply' lives in
the Ember store after clicking a non-Promoted card.

Plan:
  1. Pick a truly non-Promoted card (tertiary is null OR doesn't say 'Promoted by hirer').
  2. Snapshot every cache key.
  3. Click the card.
  4. Re-scan EVERY cache entry (not just new ones) for either:
       'clicked apply'  |  'applicant'  |  'be among the first'
     — using a SEMANTIC regex, no narrow keyword bias.
  5. For each hit, print the full URN key + the matched substring + ~60 chars of context.

If even ONE store entry has the text, the extension can read it without
relying on DOM scraping.
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278"

PICK_JS = r"""
async () => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (scroller) for (let i = 0; i < 12; i++) { scroller.scrollBy(0, 1400); await new Promise(r => setTimeout(r, 600)); }

  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    const t = (d.tertiaryDescription && d.tertiaryDescription.text) || null;
    if (t && /Promoted by hirer/i.test(t)) continue;
    const m = k.match(/\(([^,]+),/);
    return { jobId: m[1], tertiary: t };
  }
  return null;
}
"""

# Search EVERY cache entry for any 'clicked apply' OR 'applicant' OR 'be among the first'
# Return: list of {key, matches: [{phrase, around}]}
FIND_ALL_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;

  const RE = /[^"',{}\[\]]{0,80}(clicked\s+apply|applicants?\b|be\s+among\s+the\s+first)[^"',{}\[\]]{0,80}/gi;

  const out = [];
  for (const k of Object.keys(cache)) {
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { continue; }
    const ms = [...s.matchAll(RE)].map(m => m[0]).slice(0, 6);
    if (ms.length === 0) continue;
    out.push({ key: k, size: s.length, matches: ms });
  }
  return out;
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
        page.wait_for_timeout(7000)

        pick = page.evaluate(PICK_JS)
        if not pick:
            print("no non-promoted candidate found"); ctx.close(); return
        jid = pick["jobId"]
        print(f"target: {jid}  (tertiary={pick['tertiary']!r})")

        # Snapshot BEFORE
        before = page.evaluate(FIND_ALL_JS)
        before_keys = {h["key"] for h in before}
        print(f"\nBEFORE click: {len(before)} cache entries already contain applicant/clicked-apply text")

        # Click
        page.evaluate(r"""(jid) => {
          const root = document.querySelector(`[data-job-id="${jid}"]`);
          if (!root) return;
          root.scrollIntoView({block:"center"});
          const tgt = root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || root;
          tgt.click();
        }""", jid)
        page.wait_for_timeout(5000)

        # Re-scan
        after = page.evaluate(FIND_ALL_JS)
        print(f"AFTER click:  {len(after)} cache entries contain applicant/clicked-apply text")

        # Filter to entries that mention OUR specific jobId
        related = [h for h in after if jid in h["key"]]
        print(f"\n=== entries containing jobId {jid} AND applicant/click text ===")
        for h in related:
            print(f"\n  KEY: {h['key']}")
            print(f"  size: {h['size']}")
            for m in h["matches"]:
                # trim newlines
                clean = re.sub(r"\s+", " ", m)[:200]
                print(f"    ↳ {clean!r}")

        # Also: entries that are NEW (appeared after click) regardless of jobId
        new_keys = {h["key"] for h in after} - before_keys
        new_entries = [h for h in after if h["key"] in new_keys]
        print(f"\n=== entries that are NEW after click (n={len(new_entries)}) ===")
        for h in new_entries[:15]:
            print(f"\n  KEY: {h['key'][:140]}")
            for m in h["matches"][:3]:
                clean = re.sub(r"\s+", " ", m)[:200]
                print(f"    ↳ {clean!r}")

        with open("/tmp/probe_v6.json", "w") as f:
            json.dump({"target": jid, "tertiary": pick["tertiary"],
                       "before_count": len(before), "after_count": len(after),
                       "related_to_job": related, "new_entries": new_entries[:25]},
                      f, indent=2, ensure_ascii=False)
        print("\n→ full data: /tmp/probe_v6.json")
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

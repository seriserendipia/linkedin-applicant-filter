"""v4 — confirm Ember store path for non-Promoted cards after click.

For each non-Promoted card we click, snapshot:
  - tertiaryDescription.text BEFORE click
  - tertiaryDescription.text AFTER click
  - The full right-panel DOM text AFTER click
Goal: show user the exact data path so they can pick how the extension reads it.
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278"

READ_TERTIARY_JS = r"""
(jid) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const k = `urn:li:fsd_jobPostingCard:(${jid},JOB_DETAILS)`;
  const d = cache[k] && cache[k].__data;
  return {
    tertiary: d?.tertiaryDescription?.text || null,
    has_card_entry: !!d,
  };
}
"""

DETAIL_DOM_JS = "() => { const el = document.querySelector('.jobs-details') || document.querySelector('main'); return el ? el.innerText.slice(0, 800) : null; }"

SCROLL_AND_PICK_JS = r"""
async () => {
  // Scroll the list to load enough cards
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (scroller) {
    for (let i = 0; i < 20; i++) {
      scroller.scrollBy(0, 1500);
      await new Promise(r => setTimeout(r, 600));
    }
  }
  // Pick non-Promoted
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll("[data-job-id]")) {
    const jid = el.getAttribute("data-job-id");
    if (!jid || seen.has(jid)) continue; seen.add(jid);
    const txt = el.innerText || "";
    if (!/\bpromoted\b/i.test(txt)) {
      out.push({ jobId: jid, snippet: txt.slice(0, 100) });
    }
  }
  return out;
}
"""

# Apply a regex semantically for "competition signal" — count or qualitative
COUNT_RE = re.compile(
    r"(over\s+\d+|under\s+\d+|less\s+than\s+\d+|\d+)\s+(applicants?|people\s+clicked\s+apply|people\s+applied)"
    r"|be\s+among\s+the\s+first\s+(\d+\s+)?applicants?",
    re.I,
)


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

        non_p = page.evaluate(SCROLL_AND_PICK_JS)
        print(f"non-Promoted cards found: {len(non_p)}")
        for c in non_p[:8]:
            print(f"  {c['jobId']}  | {c['snippet'][:80].replace(chr(10), ' / ')}")

        targets = non_p[:6]
        print(f"\nwill probe {len(targets)} non-Promoted cards")

        results = []
        for c in targets:
            jid = c["jobId"]
            before = page.evaluate(READ_TERTIARY_JS, jid)

            page.evaluate(r"""(jid) => {
              const root = document.querySelector(`[data-job-id="${jid}"]`);
              if (!root) return;
              root.scrollIntoView({block:"center"});
              const tgt = root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || root;
              tgt.click();
            }""", jid)
            page.wait_for_timeout(4000)

            after  = page.evaluate(READ_TERTIARY_JS, jid)
            dom    = page.evaluate(DETAIL_DOM_JS) or ""

            count_in_tertiary_before = bool(before["tertiary"] and COUNT_RE.search(before["tertiary"]))
            count_in_tertiary_after  = bool(after["tertiary"]  and COUNT_RE.search(after["tertiary"]))
            count_in_dom             = bool(COUNT_RE.search(dom))
            dom_match = COUNT_RE.search(dom)

            results.append({
                "jobId": jid,
                "before_tertiary": before["tertiary"],
                "after_tertiary": after["tertiary"],
                "found_in_tertiary_before": count_in_tertiary_before,
                "found_in_tertiary_after": count_in_tertiary_after,
                "found_in_dom_after": count_in_dom,
                "dom_match": dom_match.group(0) if dom_match else None,
                "dom_head": dom[:400],
            })
            print(f"\n══ {jid} ══")
            print(f"  before tertiary: {before['tertiary']!r}")
            print(f"  after  tertiary: {after['tertiary']!r}")
            print(f"  count in DOM:    {count_in_dom}  → match={dom_match.group(0) if dom_match else None!r}")

        with open("/tmp/probe_v4.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        # Summary
        n = len(results)
        in_tert_before = sum(r["found_in_tertiary_before"] for r in results)
        in_tert_after  = sum(r["found_in_tertiary_after"]  for r in results)
        in_dom_after   = sum(r["found_in_dom_after"]       for r in results)
        print(f"\n=== SUMMARY (non-Promoted cards, n={n}) ===")
        print(f"  count visible in Ember.tertiary BEFORE click: {in_tert_before}/{n}")
        print(f"  count visible in Ember.tertiary AFTER click:  {in_tert_after}/{n}")
        print(f"  count visible in DOM AFTER click:             {in_dom_after}/{n}")
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

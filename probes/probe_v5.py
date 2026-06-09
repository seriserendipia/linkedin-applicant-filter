"""v5 — last call. Use tertiary-text ground truth to pick truly non-Promoted
cards (those whose tertiary doesn't mention 'Promoted by hirer'), then click
each and verify the Ember store gets the applicant info written in.
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

  // Scroll to load more
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (scroller) for (let i = 0; i < 12; i++) { scroller.scrollBy(0, 1400); await new Promise(r => setTimeout(r, 600)); }

  // Truly non-Promoted candidates: tertiary either null OR present but doesn't mention 'Promoted by hirer'
  const out = [];
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    const t = (d.tertiaryDescription && d.tertiaryDescription.text) || null;
    if (t && /Promoted by hirer/i.test(t)) continue;
    const m = k.match(/\(([^,]+),/);
    out.push({ jobId: m[1], tertiary: t });
  }
  return out;
}
"""

READ_TERTIARY_JS = r"""
(jid) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const k = `urn:li:fsd_jobPostingCard:(${jid},JOB_DETAILS)`;
  return cache[k] && cache[k].__data && cache[k].__data.tertiaryDescription && cache[k].__data.tertiaryDescription.text;
}
"""

DETAIL_DOM_JS = "() => { const el = document.querySelector('.jobs-details') || document.querySelector('main'); return el ? el.innerText.slice(0, 800) : null; }"

# Semantic regex covering all variants
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

        picks = page.evaluate(PICK_JS)
        print(f"non-Promoted cards (by tertiary): {len(picks)}")
        for c in picks[:10]:
            print(f"  {c['jobId']}  | {(c['tertiary'] or '<null>')[:90]!r}")

        results = []
        for c in picks[:6]:
            jid = c["jobId"]
            before = c["tertiary"]
            page.evaluate(r"""(jid) => {
              const root = document.querySelector(`[data-job-id="${jid}"]`);
              if (!root) return;
              root.scrollIntoView({block:"center"});
              const tgt = root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || root;
              tgt.click();
            }""", jid)
            page.wait_for_timeout(4000)
            after = page.evaluate(READ_TERTIARY_JS, jid)
            dom = page.evaluate(DETAIL_DOM_JS) or ""

            before_s = before or ""
            after_s  = after  or ""
            mb = COUNT_RE.search(before_s)
            ma = COUNT_RE.search(after_s)
            md = COUNT_RE.search(dom)
            results.append({
                "jobId": jid, "before": before, "after": after,
                "count_before_tertiary": mb.group(0) if mb else None,
                "count_after_tertiary":  ma.group(0) if ma else None,
                "count_in_dom":          md.group(0) if md else None,
                "dom_head": dom[:300],
            })
            print(f"\n══ {jid} (truly non-Promoted) ══")
            print(f"  tertiary BEFORE click: {(before_s or '<null>')[:120]!r}")
            print(f"  tertiary AFTER click:  {(after_s or '<null>')[:120]!r}")
            print(f"  count in DOM:          {md.group(0) if md else None!r}")
            print(f"  changed? {before != after}")

        with open("/tmp/probe_v5.json", "w") as f:
            json.dump({"picks": picks, "results": results}, f, indent=2, ensure_ascii=False)

        n = len(results)
        before_cnt = sum(1 for r in results if r["count_before_tertiary"])
        after_cnt  = sum(1 for r in results if r["count_after_tertiary"])
        dom_cnt    = sum(1 for r in results if r["count_in_dom"])
        print(f"\n=== SUMMARY (true non-Promoted, n={n}) ===")
        print(f"  count in Ember.tertiary BEFORE: {before_cnt}/{n}")
        print(f"  count in Ember.tertiary AFTER:  {after_cnt}/{n}")
        print(f"  count in DOM AFTER:             {dom_cnt}/{n}")
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

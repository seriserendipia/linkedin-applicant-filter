"""Locate the EXACT Ember cache entry + JSON path where the applicant-count
string lives after a non-Promoted card is clicked."""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278"

LOCATE_JS = r"""
(jid, needleRe) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const re = new RegExp(needleRe, "i");
  const hits = [];
  for (const k of Object.keys(cache)) {
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { continue; }
    if (!re.test(s)) continue;
    // find the JSON path of the matching string
    const paths = [];
    const walk = (obj, path) => {
      if (!obj || typeof obj !== "object") return;
      if (path.length > 8) return;
      for (const [kk, vv] of Object.entries(obj)) {
        if (typeof vv === "string" && re.test(vv)) {
          paths.push({ path: path.concat(kk).join("."), value: vv.slice(0, 200) });
        } else if (vv && typeof vv === "object") {
          walk(vv, path.concat(kk));
        }
      }
    };
    walk(d, []);
    if (paths.length) hits.push({ key: k, paths });
  }
  return hits;
}
"""

PICK_NULL_TERTIARY_JS = r"""
async () => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (scroller) for (let i = 0; i < 10; i++) { scroller.scrollBy(0, 1400); await new Promise(r => setTimeout(r, 600)); }
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    const t = d.tertiaryDescription && d.tertiaryDescription.text;
    if (!t) {
      const m = k.match(/\(([^,]+),/);
      return m[1];
    }
  }
  return null;
}
"""

NEEDLE = r"(?:over\s+\d+|under\s+\d+|less\s+than\s+\d+|\d+)\s+(?:applicants?|people\s+clicked\s+apply|people\s+applied)|be\s+among\s+the\s+first"


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, executable_path="/usr/bin/google-chrome",
            headless=False, env={**os.environ, "DISPLAY": ":99"}, no_viewport=True,
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                  "--no-first-run","--no-default-browser-check","--password-store=basic"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(7000)

        jid = page.evaluate(PICK_NULL_TERTIARY_JS)
        print(f"target non-Promoted (null tertiary): {jid}")
        if not jid:
            ctx.close(); raise SystemExit("no candidate")

        # click it
        page.evaluate(r"""(jid) => {
          const root = document.querySelector(`[data-job-id="${jid}"]`);
          if (!root) return;
          root.scrollIntoView({block:"center"});
          (root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || root).click();
        }""", jid)
        page.wait_for_timeout(5500)

        hits = page.evaluate(LOCATE_JS, [jid, NEEDLE])
        print(f"\n=== cache hits for jobId={jid} ===")
        for h in hits:
            print(f"\n{h['key']}")
            for p in h["paths"]:
                print(f"  .{p['path']}  =  {p['value']!r}")

        with open("/tmp/locate.json", "w") as f:
            json.dump({"jobId": jid, "hits": hits}, f, indent=2, ensure_ascii=False)
        ctx.close()


if __name__ == "__main__":
    main()

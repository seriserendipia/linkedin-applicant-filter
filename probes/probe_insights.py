"""Dump the FULL content of fsd_jobApplicantInsights entries to find where
the actual applicant count number lives.

Strategy:
  1. Load page
  2. Click 5 different non-Promoted cards in sequence so LinkedIn loads
     applicant insights for each
  3. Dump every fsd_jobApplicantInsights:* entry in full
  4. Also dump the DOM text of the right-side detail panel
"""
import json, os, re, sys
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20engineer%20intern&geoId=103644278"

DUMP_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const out = {};
  for (const k of Object.keys(cache)) {
    if (k.includes("fsd_jobApplicantInsights:")) {
      out[k] = cache[k] && cache[k].__data;
    }
  }
  // also any fsd_jobPostingCard whose tertiary mentions applicants
  const cardsWithApp = {};
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    const t = d && d.tertiaryDescription && d.tertiaryDescription.text;
    if (t && /applicant/i.test(t)) cardsWithApp[k] = t;
  }
  // DOM right-panel text — anything mentioning applicant
  const domSnippets = [];
  document.querySelectorAll(".jobs-details, .jobs-unified-top-card, .job-details-jobs-unified-top-card, main").forEach(n => {
    const t = n.innerText || "";
    if (/applicant/i.test(t)) {
      // grab the line(s) containing 'applicant'
      const lines = t.split("\n").filter(l => /applicant/i.test(l));
      domSnippets.push(...lines.map(l => l.trim()));
    }
  });
  return { insights: out, cardsWithApp, domSnippets: [...new Set(domSnippets)].slice(0, 10) };
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

        # Click 8 cards in sequence
        ids_clicked = page.evaluate(r"""async () => {
          await new Promise(r => setTimeout(r, 500));
          const list = document.querySelectorAll("[data-job-id]");
          const ids = [];
          const seen = new Set();
          for (const el of list) {
            const jid = el.getAttribute("data-job-id");
            if (!jid || seen.has(jid)) continue;
            seen.add(jid);
            el.scrollIntoView({block:"center"});
            // try clicking either a child link or the element itself
            const target = el.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || el;
            target.click();
            ids.push(jid);
            await new Promise(r => setTimeout(r, 1500));
            if (ids.length >= 8) break;
          }
          return ids;
        }""")
        print(f"clicked through {len(ids_clicked)} cards: {ids_clicked}")
        page.wait_for_timeout(3000)

        dump = page.evaluate(DUMP_JS)
        print(f"--- fsd_jobApplicantInsights entries: {len(dump['insights'])} ---")
        for k, v in dump["insights"].items():
            print(f"\n{k}:")
            print(json.dumps(v, indent=2, ensure_ascii=False)[:1200])
        print(f"\n--- cards with tertiary applicant text: {len(dump['cardsWithApp'])} ---")
        for k, t in list(dump["cardsWithApp"].items())[:15]:
            jid = re.search(r"\(([^,]+),", k).group(1)
            m = re.search(r"(over\s+\d+|\d+\+?|less than \d+)\s+applicants?", t, re.I)
            print(f"  {jid}  →  {m.group(0) if m else '?'!r}  | {t[:80]!r}")
        print(f"\n--- DOM applicant lines: {len(dump['domSnippets'])} ---")
        for s in dump["domSnippets"]:
            print(f"  {s!r}")

        with open("/tmp/insights_dump.json", "w") as f:
            json.dump(dump, f, indent=2, ensure_ascii=False)
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()

if __name__ == "__main__":
    main()

"""Round-2 probe — no keyword bias.

User says clicking a card DOES surface applicant info in the right panel,
in many possible wordings ("47 applicants", "Over 100", "27 people clicked apply",
"Be among the first to apply", "<25 applicants", etc.).

So this time:
  1. Click 6 non-Promoted cards
  2. After each click, dump the FULL inner text of the right detail panel,
     verbatim, so we can see exactly what LinkedIn renders
  3. Also diff the Ember cache and dump every new entry whose stringified
     payload contains "apply" or any 1-3 digit number followed by a noun
  4. Save the per-card detail texts for the user to inspect raw
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278"

# Read the FULL detail panel innerText, no filtering.
DETAIL_TEXT_JS = r"""
() => {
  const sels = [
    ".jobs-details",
    ".job-details-jobs-unified-top-card",
    ".jobs-unified-top-card",
    ".job-view-layout",
    "main",
  ];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el && el.innerText && el.innerText.length > 100) {
      return { selector: s, text: el.innerText };
    }
  }
  return null;
}
"""

# Pull every cache entry related to one jobId AND every string field anywhere
# in those entries that matches a "<number> <something>" pattern.
DEEP_INSPECT_JS = r"""
(jobId) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;

  const NUM_PHRASE = /\b(\d{1,4}\+?|over\s+\d+|less than \d+|under \d+|be among the first)\b[^.\n]{0,80}/gi;

  const interesting = [];
  for (const k of Object.keys(cache)) {
    if (!k.includes(jobId)) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { continue; }
    const matches = [...s.matchAll(NUM_PHRASE)].map(m => m[0]).slice(0, 8);
    if (matches.length === 0) continue;
    interesting.push({ key: k, sample_matches: matches });
  }
  return interesting;
}
"""

CACHE_KEYS_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  return Object.keys(cache);
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

        # Pick 6 candidate cards: any data-job-id we can find, prefer ones whose
        # tertiary text doesn't already include 'applicant'/'clicked apply'.
        candidates = page.evaluate(r"""() => {
          const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
          const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
          const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
          const out = [];
          // walk DOM order so we pick what's actually visible
          const seen = new Set();
          document.querySelectorAll("[data-job-id]").forEach(el => {
            const jid = el.getAttribute("data-job-id");
            if (!jid || seen.has(jid)) return; seen.add(jid);
            const k = `urn:li:fsd_jobPostingCard:(${jid},JOB_DETAILS)`;
            const t = cache[k] && cache[k].__data && cache[k].__data.tertiaryDescription
                       && cache[k].__data.tertiaryDescription.text;
            out.push({ jobId: jid, tertiary: t || "" });
          });
          return out;
        }""")
        print(f"candidates on page: {len(candidates)}")
        # Prefer cards with no applicant/click info in tertiary
        wanted = [c for c in candidates if not re.search(r"applicant|clicked apply", c["tertiary"], re.I)]
        wanted = wanted[:6] if wanted else candidates[:6]
        print(f"will click: {[c['jobId'] for c in wanted]}")

        results = []
        for c in wanted:
            jid = c["jobId"]
            before_keys = set(page.evaluate(CACHE_KEYS_JS))
            clicked = page.evaluate(
                r"""(jid) => {
                  const root = document.querySelector(`[data-job-id="${jid}"]`);
                  if (!root) return "no-root";
                  root.scrollIntoView({block:"center"});
                  // try child clickable; otherwise root itself
                  const tgt = root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable")
                            || root;
                  tgt.click();
                  return "clicked";
                }""", jid)
            page.wait_for_timeout(4000)
            after_keys = set(page.evaluate(CACHE_KEYS_JS))
            new_keys = sorted(after_keys - before_keys)

            detail = page.evaluate(DETAIL_TEXT_JS)
            deep = page.evaluate(DEEP_INSPECT_JS, jid)

            entry = {
                "jobId": jid,
                "tertiary_before": c["tertiary"][:120],
                "click_status": clicked,
                "new_cache_keys": new_keys,
                "detail_panel_text": (detail or {}).get("text", "")[:4000],
                "detail_selector": (detail or {}).get("selector"),
                "cache_numeric_matches": deep,
            }
            results.append(entry)
            print(f"\n══ {jid} ══")
            print(f"  before tertiary: {c['tertiary'][:100]!r}")
            print(f"  new keys: {len(new_keys)}")
            print(f"  detail panel ({entry['detail_selector']}, {len(entry['detail_panel_text'])} chars):")
            # show first 30 lines of detail panel
            lines = entry["detail_panel_text"].split("\n")
            for ln in lines[:30]:
                if ln.strip():
                    print(f"    | {ln.strip()[:140]}")

        with open("/tmp/probe_v2.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print("\n→ full data: /tmp/probe_v2.json")
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

"""γ feasibility probe — does LinkedIn return applicant count in the SSR HTML
of /jobs/view/${jobId} when we fetch it with the user's session cookies?

We use Playwright's context.request which carries the same cookie jar as the
page, so it mimics what background.js + fetch(credentials:'include') would
get from the extension.
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20scientist&geoId=103644278"

# Get visible job IDs split by promoted vs non-promoted
PICK_JS = r"""
async () => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  // scroll once to load more
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (scroller) for (let i = 0; i < 8; i++) { scroller.scrollBy(0, 1200); await new Promise(r => setTimeout(r, 500)); }

  const promoted = [], nonPromoted = [];
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    const t = (d.tertiaryDescription && d.tertiaryDescription.text) || null;
    const m = k.match(/\(([^,]+),/);
    const entry = { jobId: m[1], tertiary: t };
    if (t && /Promoted by hirer/i.test(t)) promoted.push(entry);
    else nonPromoted.push(entry);
  }
  return { promoted: promoted.slice(0, 3), nonPromoted: nonPromoted.slice(0, 5) };
}
"""

# Same semantic regex as before
COUNT_RE = re.compile(
    r"(over\s+\d+|under\s+\d+|less\s+than\s+\d+|\d{1,4})\s+(applicants?|people\s+(?:clicked\s+apply|applied))"
    r"|be\s+among\s+the\s+first(?:\s+\d+)?\s+applicants?",
    re.I,
)


def analyse_html(html: str, jid: str):
    """Look for applicant count in the raw HTML."""
    findings = {
        "html_size": len(html),
        "matches": [],
        "raw_count_phrases": [],
    }
    # Strict semantic regex
    for m in COUNT_RE.finditer(html):
        # grab ~80 chars of context
        i = m.start()
        ctx = html[max(0, i - 80) : i + len(m.group(0)) + 80]
        ctx = re.sub(r"\s+", " ", ctx)
        findings["matches"].append({"phrase": m.group(0), "ctx": ctx[:240]})
    # Looser: any phrase like "N applicants" or "N people"
    loose_re = re.compile(r"\b\d{1,4}\+?\s+(?:applicants?|people\b)", re.I)
    findings["raw_count_phrases"] = list(set(m.group(0) for m in loose_re.finditer(html)))[:20]
    # Check for the JSON data island pattern LinkedIn often uses
    findings["has_code_island"] = "<code style=\"display: none\"" in html or "<code id=" in html
    findings["likely_login_wall"] = "uas/login" in html or "guest_homepage" in html
    findings["likely_auth_wall"] = "authwall" in html.lower()
    return findings


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
        promoted = picks["promoted"]
        non_promoted = picks["nonPromoted"]
        print(f"will fetch: promoted={len(promoted)}, non_promoted={len(non_promoted)}")

        results = []
        # Use the page's request context (carries cookies + UA)
        req = ctx.request
        for batch_name, items in [("PROMOTED", promoted), ("NON-PROMOTED", non_promoted)]:
            print(f"\n{'═'*60}\n  {batch_name}\n{'═'*60}")
            for c in items:
                jid = c["jobId"]
                url = f"https://www.linkedin.com/jobs/view/{jid}/"
                try:
                    r = req.get(url, headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                      "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    }, timeout=15_000)
                    status = r.status
                    html = r.text() if status == 200 else ""
                except Exception as e:
                    status, html = -1, ""
                    print(f"  {jid}  FETCH ERROR: {e}")
                    continue

                a = analyse_html(html, jid)
                results.append({"jid": jid, "batch": batch_name, "status": status,
                                "tertiary_in_list": c["tertiary"], **a})
                print(f"\n  ── {jid} ── status={status} size={a['html_size']}")
                print(f"     login_wall={a['likely_login_wall']}  auth_wall={a['likely_auth_wall']}")
                if c["tertiary"]:
                    ground = COUNT_RE.search(c["tertiary"])
                    print(f"     list-cache tertiary count: {ground.group(0) if ground else None!r}")
                print(f"     loose count phrases in HTML: {a['raw_count_phrases'][:8]}")
                if a["matches"]:
                    print(f"     STRICT MATCHES IN HTML (n={len(a['matches'])}):")
                    for m in a["matches"][:5]:
                        print(f"        ↳ {m['phrase']!r}")
                        print(f"           ctx: {m['ctx']!r}")
                else:
                    print(f"     ❌ no strict applicant-count match found in HTML")

        with open("/tmp/probe_fetch.json", "w") as f:
            # Don't dump raw HTML, too big
            for r in results: r.pop("html", None)
            json.dump(results, f, indent=2, ensure_ascii=False)
        print("\n→ /tmp/probe_fetch.json")

        # Summary
        ok_strict = sum(1 for r in results if r["matches"])
        ok_loose  = sum(1 for r in results if r["raw_count_phrases"])
        n = len(results)
        print(f"\n=== SUMMARY (n={n}) ===")
        print(f"  HTML had strict applicant-count match: {ok_strict}/{n}")
        print(f"  HTML had any loose 'N applicants/people' phrase: {ok_loose}/{n}")
        ctx.close()


if __name__ == "__main__":
    main()

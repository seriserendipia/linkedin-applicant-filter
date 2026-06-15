"""Investigate the AI/semantic search surface
(/jobs/search-results/?origin=SEMANTIC_SEARCH_LANDING_PAGE).

Tries two ways to reach it:
  1. Direct nav to the user-supplied semantic URL.
  2. If that redirects/doesn't show the semantic layout, drive the UI:
     go to /jobs, type a keyword in the search box, submit, then look for an
     "AI" / semantic entry point.

For whichever page we land on, dump the same diagnostic the user would run.
"""
import json, os, time
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"

SEMANTIC_URL = ("https://www.linkedin.com/jobs/search-results/?currentJobId=4395147415"
                "&keywords=ai%20engineer&origin=SEMANTIC_SEARCH_LANDING_PAGE")

PROBE_JS = r"""
() => {
  const out = { path: location.pathname, search: location.search.slice(0, 80),
                semantic: location.search.includes('SEMANTIC') };
  // A. Ember store
  try {
    const E = window.requireModule ? window.requireModule("ember").default : window.Ember;
    if (!E) out.ember = "no window.Ember";
    else {
      const app = E.Namespace.NAMESPACES.find(n => n instanceof E.Application);
      const cache = app && app.__container__.lookup("service:store")._globalM3RecordDataCache;
      if (!cache) out.ember = "no cache";
      else {
        const keys = Object.keys(cache);
        out.emberCardKeys = keys.filter(k => k.includes("fsd_jobPostingCard:")).length;
        out.emberPostingKeys = keys.filter(k => k.includes("fsd_jobPosting:")).length;
        const samples = [];
        for (const k of keys) {
          if (k.includes("fsd_jobPostingCard:") && k.includes("JOB_DETAILS")) {
            const t = cache[k].__data && cache[k].__data.tertiaryDescription
                       && cache[k].__data.tertiaryDescription.text;
            if (t) samples.push((k.match(/\((\d+),/)?.[1] || "?") + " => " + t.slice(0, 55));
            if (samples.length >= 5) break;
          }
        }
        out.emberSamples = samples;
      }
    }
  } catch (e) { out.emberErr = String(e).slice(0, 100); }

  // B. applicant texts in DOM
  const appEls = [];
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length === 0) {
      const t = (el.textContent || '').trim();
      if (/\d+\s+(applicant|people)|over \d+\s+(applicant|people)|be among the first/i.test(t) && t.length < 60)
        appEls.push(t);
    }
  });
  out.applicantTexts = [...new Set(appEls)].slice(0, 15);

  // C. jobId-like numbers in attributes
  const ids = new Set();
  document.querySelectorAll('[href],[id],[data-test-app-aware-link],[aria-label]').forEach(el => {
    for (const a of ['href','id','aria-label']) {
      const m = (el.getAttribute(a) || '').match(/(\d{8,12})/);
      if (m) ids.add(m[1]);
    }
  });
  out.jobIdLikeCount = ids.size;
  out.sampleJobIds = [...ids].slice(0, 8);

  // D. card container signals
  out.li = document.querySelectorAll('li').length;
  out.roleButton = document.querySelectorAll('[role="button"]').length;
  out.tabindex = document.querySelectorAll('[tabindex]').length;
  out.appAwareLinks = document.querySelectorAll('[data-test-app-aware-link]').length;
  out.jobViewLinks = document.querySelectorAll('a[href*="/jobs/view/"]').length;
  out.sduiScreen = document.querySelectorAll('div[data-sdui-screen]').length;
  out.searchResultsMain = document.querySelectorAll('div[componentkey="SearchResultsMainContent"]').length;
  out.scaffoldList = document.querySelectorAll('.scaffold-layout__list').length;

  return out;
}
"""


def dump(page, tag):
    try:
        r = page.evaluate(PROBE_JS)
    except Exception as e:
        r = {"probe_error": str(e)[:120]}
    print(f"\n===== {tag} =====")
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return r


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

        # Attempt 1: direct semantic URL
        print(f"→ direct nav to semantic URL")
        page.goto(SEMANTIC_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(7000)
        print(f"landed at: {page.url[:120]}")
        r1 = dump(page, "DIRECT SEMANTIC URL")
        page.screenshot(path="/usr/share/novnc/shots/sem1.png")

        # Attempt 2: drive the UI from /jobs
        print(f"\n→ driving UI from /jobs home")
        page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(5000)
        # Find a jobs search keyword box and type
        typed = False
        for sel in ['input[aria-label*="Search by title"]',
                    'input[id*="jobs-search-box-keyword"]',
                    'input[aria-label*="Search jobs"]',
                    '.jobs-search-box__text-input[aria-label*="title"]',
                    'input[placeholder*="Search"]']:
            try:
                el = page.query_selector(sel)
                if el:
                    el.click(); el.fill("ai engineer")
                    page.keyboard.press("Enter")
                    typed = True
                    print(f"   typed into {sel}")
                    break
            except Exception:
                pass
        if not typed:
            print("   could not find a search box")
        page.wait_for_timeout(7000)
        print(f"landed at: {page.url[:120]}")
        r2 = dump(page, "AFTER UI SEARCH FROM /jobs")
        page.screenshot(path="/usr/share/novnc/shots/sem2.png")

        with open("/tmp/probe_semantic.json", "w") as f:
            json.dump({"direct": r1, "ui_search": r2,
                       "direct_url": page.url}, f, indent=2, ensure_ascii=False)
        print("\n→ /tmp/probe_semantic.json")
        print("→ screenshots: /usr/share/novnc/shots/sem1.png , sem2.png")
        ctx.close()


if __name__ == "__main__":
    main()

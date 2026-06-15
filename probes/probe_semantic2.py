"""Dig into the semantic page's <li> cards to find the per-card jobId.
Checks: data-* attributes, nested hrefs, and React fiber/props (LinkedIn's
SDUI is React — the jobId is likely in memoizedProps/pendingProps)."""
import json, os
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
SEMANTIC_URL = ("https://www.linkedin.com/jobs/search-results/?currentJobId=4395147415"
                "&keywords=ai%20engineer&origin=SEMANTIC_SEARCH_LANDING_PAGE")

PROBE = r"""
() => {
  const root = document.querySelector('div[componentkey="SearchResultsMainContent"]')
            || document.querySelector('div[data-sdui-screen]')
            || document;
  // candidate cards: <li> inside the results region
  const lis = [...root.querySelectorAll('li')].slice(0, 6);
  const reactKey = (el, pfx) => Object.keys(el).find(k => k.startsWith(pfx));

  function fiberJobHints(el) {
    const hints = [];
    const fk = reactKey(el, '__reactFiber$') || reactKey(el, '__reactInternalInstance$');
    const pk = reactKey(el, '__reactProps$');
    const scan = (obj, depth, path) => {
      if (!obj || typeof obj !== 'object' || depth > 4) return;
      for (const [k, v] of Object.entries(obj)) {
        if (hints.length > 8) return;
        if (typeof v === 'string') {
          if (/urn:li:.*[jJ]ob/.test(v) || /^\d{8,12}$/.test(v) || /jobPosting/i.test(v))
            hints.push(path + '.' + k + ' = ' + v.slice(0, 60));
        } else if (v && typeof v === 'object' && depth < 4) {
          scan(v, depth + 1, path + '.' + k);
        }
      }
    };
    if (pk) scan(el[pk], 0, 'props');
    if (fk) {
      let f = el[fk], hops = 0;
      while (f && hops < 6) {
        if (f.memoizedProps) scan(f.memoizedProps, 0, 'fiber[' + hops + '].memoizedProps');
        f = f.return; hops++;
      }
    }
    return hints;
  }

  const cards = lis.map((li, i) => {
    const dataAttrs = {};
    for (const a of li.attributes) if (a.name.startsWith('data-') || a.name === 'id') dataAttrs[a.name] = a.value;
    const a = li.querySelector('a[href]');
    const txt = (li.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    // also scan descendants' attributes for digit ids
    const descIds = new Set();
    li.querySelectorAll('[href],[id],[data-test-app-aware-link],[aria-label]').forEach(e => {
      for (const at of ['href','id','aria-label']) {
        const m = (e.getAttribute(at) || '').match(/(\d{8,12})/);
        if (m) descIds.add(m[1]);
      }
    });
    return {
      i, dataAttrs,
      anchorHref: a ? a.getAttribute('href').slice(0, 70) : null,
      descIds: [...descIds].slice(0, 4),
      fiberHints: fiberJobHints(li),
      text: txt,
    };
  });
  return { liCount: root.querySelectorAll('li').length, cards };
}
"""


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
        page.goto(SEMANTIC_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(8000)
        r = page.evaluate(PROBE)
        print(json.dumps(r, indent=2, ensure_ascii=False)[:5000])
        with open("/tmp/probe_semantic2.json", "w") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        ctx.close()


if __name__ == "__main__":
    main()

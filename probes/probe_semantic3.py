"""Locate the real semantic-page job card by finding which DOM element's React
fiber/props contains the known currentJobId, then learn the repeated card
structure + how to read every card's jobId."""
import json, os
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
KNOWN_ID = "4395147415"
SEMANTIC_URL = (f"https://www.linkedin.com/jobs/search-results/?currentJobId={KNOWN_ID}"
                "&keywords=ai%20engineer&origin=SEMANTIC_SEARCH_LANDING_PAGE")

PROBE = r"""
(KNOWN) => {
  const rk = (el, pfx) => Object.keys(el).find(k => k.startsWith(pfx));
  function fiberContains(el, needle) {
    // returns the prop path where needle is found, else null
    const seen = new Set();
    function scan(obj, depth, path) {
      if (!obj || typeof obj !== 'object' || seen.has(obj) || depth > 5) return null;
      seen.add(obj);
      for (const [k, v] of Object.entries(obj)) {
        if (typeof v === 'string' && v.includes(needle)) return path + '.' + k + '=' + v.slice(0,70);
        if (v && typeof v === 'object') { const r = scan(v, depth+1, path+'.'+k); if (r) return r; }
      }
      return null;
    }
    const pk = rk(el, '__reactProps$');
    if (pk) { const r = scan(el[pk], 0, 'props'); if (r) return r; }
    const fk = rk(el, '__reactFiber$') || rk(el, '__reactInternalInstance$');
    if (fk) {
      let f = el[fk], hops = 0;
      while (f && hops < 4) {
        if (f.memoizedProps) { const r = scan(f.memoizedProps, 0, 'fiber'+hops+'.props'); if (r) return r; }
        f = f.return; hops++;
      }
    }
    return null;
  }

  // 1. Find the smallest element containing the known id in fiber/props
  const all = document.querySelectorAll('div[componentkey="SearchResultsMainContent"] *');
  let hit = null;
  for (const el of all) {
    const p = fiberContains(el, KNOWN);
    if (p) { hit = { el, path: p }; break; }
  }
  if (!hit) return { found: false, scanned: all.length };

  // 2. Walk up to find the "card" — the element whose siblings are also cards.
  // Heuristic: climb until parent has >= 5 children that each contain a job id.
  function idOf(el) {
    // try fiber path generically: look for urn:li:fsd_jobPosting:NNN or 8-12 digit
    const rkp = rk(el, '__reactProps$');
    const fkp = rk(el, '__reactFiber$');
    const seen = new Set();
    function find(obj, depth) {
      if (!obj || typeof obj !== 'object' || seen.has(obj) || depth > 5) return null;
      seen.add(obj);
      for (const v of Object.values(obj)) {
        if (typeof v === 'string') {
          const m = v.match(/urn:li:fsd_jobPosting(?:Card)?:\(?(\d{6,})/) || v.match(/\/jobs\/view\/(\d{6,})/);
          if (m) return m[1];
        } else if (v && typeof v === 'object') { const r = find(v, depth+1); if (r) return r; }
      }
      return null;
    }
    if (rkp) { const r = find(el[rkp], 0); if (r) return r; }
    if (fkp) { let f = el[fkp], h = 0; while (f && h < 4) { if (f.memoizedProps) { const r = find(f.memoizedProps, 0); if (r) return r; } f = f.return; h++; } }
    return null;
  }

  let card = hit.el, best = null;
  for (let i = 0; i < 12 && card && card.parentElement; i++) {
    const parent = card.parentElement;
    const sibs = [...parent.children];
    const withId = sibs.filter(s => idOf(s)).length;
    if (withId >= 5) { best = { cardTag: card.tagName, cardCls: (card.className||'').toString().slice(0,60),
                                parentChildren: sibs.length, siblingsWithId: withId, climbed: i }; break; }
    card = parent;
  }

  // 3. Extract ids from the card's siblings
  let ids = [];
  if (best && card && card.parentElement) {
    ids = [...card.parentElement.children].map(idOf).filter(Boolean).slice(0, 12);
  }

  return {
    found: true,
    hitPath: hit.path,
    hitTag: hit.el.tagName,
    bestCard: best,
    extractedIds: ids,
    extractedCount: ids.length,
  };
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
        r = page.evaluate(PROBE, KNOWN_ID)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        with open("/tmp/probe_semantic3.json", "w") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        ctx.close()


if __name__ == "__main__":
    main()

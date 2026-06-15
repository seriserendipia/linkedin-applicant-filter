"""Crack the semantic-page card enumeration:
  A. all componentkey values inside SearchResultsMainContent
  B. broadened fiber jobId extraction (JobCardFrameworkImpl...State_{id} etc),
     collect (element, jobId) across the page, report how many + the carrier
     element's tag/componentkey/class
  C. ancestry of one card so we can pick a stable selector for the card unit."""
import json, os
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
KNOWN_ID = "4395147415"
SEMANTIC_URL = (f"https://www.linkedin.com/jobs/search-results/?currentJobId={KNOWN_ID}"
                "&keywords=ai%20engineer&origin=SEMANTIC_SEARCH_LANDING_PAGE")

PROBE = r"""
() => {
  const root = document.querySelector('div[componentkey="SearchResultsMainContent"]');
  const out = {};

  // A. componentkey census
  const ckCount = {};
  root.querySelectorAll('[componentkey]').forEach(e => {
    const k = e.getAttribute('componentkey');
    // normalize trailing digits so per-card keys collapse
    const norm = k.replace(/\d{6,}/g, '{ID}');
    ckCount[norm] = (ckCount[norm] || 0) + 1;
  });
  out.componentkeyCensus = Object.entries(ckCount).sort((a,b)=>b[1]-a[1]).slice(0, 20);

  // B. broadened jobId extractor over fiber/props
  const rk = (el, pfx) => Object.keys(el).find(k => k.startsWith(pfx));
  const ID_RES = [
    /JobCardFrameworkImpl\w*State_(\d{6,})/,
    /urn:li:fsd_jobPosting(?:Card)?:\(?(\d{6,})/,
    /\/jobs\/view\/(\d{6,})/,
    /jobPostingCard:\((\d{6,})/,
  ];
  function idOf(el) {
    const seen = new Set();
    function find(obj, depth) {
      if (!obj || typeof obj !== 'object' || seen.has(obj) || depth > 5) return null;
      seen.add(obj);
      for (const v of Object.values(obj)) {
        if (typeof v === 'string') {
          for (const re of ID_RES) { const m = v.match(re); if (m) return m[1]; }
        } else if (v && typeof v === 'object') { const r = find(v, depth+1); if (r) return r; }
      }
      return null;
    }
    const pk = rk(el, '__reactProps$');
    if (pk) { const r = find(el[pk], 0); if (r) return r; }
    const fk = rk(el, '__reactFiber$') || rk(el, '__reactInternalInstance$');
    if (fk) { let f = el[fk], h = 0; while (f && h < 4) { if (f.memoizedProps) { const r = find(f.memoizedProps, 0); if (r) return r; } f = f.return; h++; } }
    return null;
  }

  // Find the carrier elements that directly resolve an id, dedup by id, keep
  // the SHALLOWEST (largest) element per id as the card.
  const byId = new Map();
  const els = root.querySelectorAll('div');
  for (const el of els) {
    const id = idOf(el);
    if (!id) continue;
    if (!byId.has(id)) byId.set(id, el);
  }
  out.distinctIds = byId.size;
  out.sampleIds = [...byId.keys()].slice(0, 12);

  // For the first 3 ids, describe the carrier element + a stable-looking ancestor
  out.carriers = [...byId.entries()].slice(0, 3).map(([id, el]) => {
    const anc = [];
    let e = el;
    for (let i = 0; i < 6 && e && e !== root; i++) {
      anc.push({ tag: e.tagName, ck: e.getAttribute('componentkey'),
                 dvn: e.getAttribute('data-view-name'),
                 cls: (e.className||'').toString().slice(0,40) });
      e = e.parentElement;
    }
    return { id, text: (el.innerText||'').replace(/\s+/g,' ').trim().slice(0,60), ancestry: anc };
  });

  return out;
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
        print(json.dumps(r, indent=2, ensure_ascii=False)[:6000])
        with open("/tmp/probe_semantic4.json", "w") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        ctx.close()


if __name__ == "__main__":
    main()

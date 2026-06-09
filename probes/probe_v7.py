"""v7 — full forensic trace of what happens on click.

Snapshot:
  - URL, DOM right-panel head, ALL cache keys (count + set)
Click a non-Promoted card.
Wait 6s.
Re-snapshot.
For every NEW cache key, dump its first 600 chars to see what landed.
Then also semantic-grep the new content for click/applicant patterns.
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20scientist&geoId=103644278"  # fresh query

PICK_JS = r"""
async () => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (scroller) for (let i = 0; i < 10; i++) { scroller.scrollBy(0, 1200); await new Promise(r => setTimeout(r, 600)); }
  for (const k of Object.keys(cache)) {
    if (!k.includes("fsd_jobPostingCard:") || !k.includes(",JOB_DETAILS)")) continue;
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    const t = (d.tertiaryDescription && d.tertiaryDescription.text) || null;
    if (t && /Promoted by hirer/i.test(t)) continue;
    const m = k.match(/\(([^,]+),/);
    return { jobId: m[1], tertiary: t };
  }
  return null;
}
"""

SNAPSHOT_JS = r"""
() => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const det = document.querySelector(".jobs-details") || document.querySelector("main");
  return {
    keys: Object.keys(cache),
    url: location.href,
    panelHead: det ? det.innerText.slice(0, 400) : null,
  };
}
"""

# Dump first N chars of given keys
DUMP_KEYS_JS = r"""
(keys) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const out = {};
  for (const k of keys) {
    const d = cache[k] && cache[k].__data;
    if (!d) { out[k] = null; continue; }
    let s; try { s = JSON.stringify(d); } catch { s = String(d); }
    out[k] = { len: s.length, head: s.slice(0, 800) };
  }
  return out;
}
"""

SEMANTIC_RE = re.compile(r"(over\s+\d+|under\s+\d+|less\s+than\s+\d+|\d{1,4})\s+(applicants?|people\s+(?:clicked\s+apply|applied))|be\s+among\s+the\s+first(?:\s+\d+)?\s+applicants?", re.I)


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

        pick = page.evaluate(PICK_JS)
        if not pick:
            print("no non-promoted candidate found"); ctx.close(); return
        jid = pick["jobId"]
        print(f"target: {jid}  tertiary={pick['tertiary']!r}")

        snap_before = page.evaluate(SNAPSHOT_JS)
        print(f"BEFORE: keys={len(snap_before['keys'])}  url={snap_before['url'][:120]}")
        print(f"  panel head: {(snap_before['panelHead'] or '')[:200]!r}")

        # Click
        click_status = page.evaluate(r"""(jid) => {
          const root = document.querySelector(`[data-job-id="${jid}"]`);
          if (!root) return "no-root";
          root.scrollIntoView({block:"center"});
          const tgt = root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || root;
          tgt.click();
          return "clicked";
        }""", jid)
        print(f"\nclick status: {click_status}")
        page.wait_for_timeout(6000)

        snap_after = page.evaluate(SNAPSHOT_JS)
        print(f"\nAFTER:  keys={len(snap_after['keys'])}  url={snap_after['url'][:120]}")
        print(f"  panel head: {(snap_after['panelHead'] or '')[:300]!r}")
        new_keys = sorted(set(snap_after["keys"]) - set(snap_before["keys"]))
        print(f"\nnew keys: {len(new_keys)}")

        # Filter new keys that mention our jobId
        related_new = [k for k in new_keys if jid in k or jid.replace(",", "%2C") in k]
        print(f"new keys mentioning jobId {jid}: {len(related_new)}")
        for k in related_new:
            print(f"  {k[:180]}")

        # Dump full content of those keys
        dumps = page.evaluate(DUMP_KEYS_JS, related_new[:30])
        print("\n=== content of each new related key ===")
        applicant_hits_in_cache = []
        for k, info in dumps.items():
            if not info: continue
            head = info["head"]
            m = SEMANTIC_RE.search(head)
            hit = m.group(0) if m else None
            if hit:
                applicant_hits_in_cache.append((k, hit))
            print(f"\n  ▸ {k[:180]}")
            print(f"    len={info['len']}  semantic_hit={hit!r}")
            print(f"    head: {head[:400]!r}")

        print(f"\n=== APPLICANT HITS IN CACHE ===")
        for k, h in applicant_hits_in_cache:
            print(f"  {h!r}  ← {k[:140]}")

        # Final: does the DOM contain the count?
        dom_match = SEMANTIC_RE.search(snap_after["panelHead"] or "")
        print(f"\ncount in DOM after click: {dom_match.group(0) if dom_match else None!r}")

        with open("/tmp/probe_v7.json", "w") as f:
            json.dump({"jobId": jid, "before": snap_before, "after_url": snap_after["url"],
                       "after_panel": snap_after["panelHead"], "new_keys": new_keys,
                       "related_dumps": dumps, "cache_hits": applicant_hits_in_cache,
                       "dom_hit": dom_match.group(0) if dom_match else None}, f, indent=2, ensure_ascii=False)
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()

if __name__ == "__main__":
    main()

"""v8 — search the live cache for the EXACT applicant-count strings visible
in the DOM. We use the panel innerText as ground truth: whatever number it
shows, search for that literal string across every cache entry.
"""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20scientist&geoId=103644278"

GET_PANEL_JS = "() => { const el = document.querySelector('.jobs-details') || document.querySelector('main'); return el ? el.innerText : null; }"

# Given an exact substring, find which cache entries contain it.
LOCATE_JS = r"""
(needle) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const out = [];
  for (const k of Object.keys(cache)) {
    const d = cache[k] && cache[k].__data;
    if (!d) continue;
    let s; try { s = JSON.stringify(d); } catch { continue; }
    const idx = s.indexOf(needle);
    if (idx === -1) continue;
    out.push({ key: k, ctx: s.slice(Math.max(0, idx-100), idx + needle.length + 100) });
  }
  return out;
}
"""

# After we locate the URN, dig into its full structure to find the FIELD PATH
PATH_JS = r"""
(key, needle) => {
  const Ember = window.requireModule ? window.requireModule("ember").default : window.Ember;
  const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
  const cache = app.__container__.lookup("service:store")._globalM3RecordDataCache;
  const d = cache[key] && cache[key].__data;
  if (!d) return null;
  const paths = [];
  const seen = new WeakSet();
  const walk = (o, path) => {
    if (o == null || typeof o !== "object" || seen.has(o)) return;
    seen.add(o);
    if (path.length > 10) return;
    if (Array.isArray(o)) {
      o.forEach((v, i) => walk(v, path.concat(`[${i}]`)));
    } else {
      for (const [k, v] of Object.entries(o)) {
        if (typeof v === "string" && v.includes(needle)) paths.push({ path: path.concat(k).join("."), value: v });
        else walk(v, path.concat(k));
      }
    }
  };
  walk(d, []);
  return paths;
}
"""

COUNT_RE = re.compile(r"(over\s+\d+|under\s+\d+|less\s+than\s+\d+|\d{1,4})\s+(applicants?|people\s+(?:clicked\s+apply|applied))|be\s+among\s+the\s+first(?:\s+\d+)?\s+applicants?", re.I)

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
        page.wait_for_timeout(8000)

        # Get the panel text
        panel = page.evaluate(GET_PANEL_JS) or ""
        m = COUNT_RE.search(panel)
        if not m:
            print(f"no count phrase in panel. panel head:\n{panel[:500]}")
            ctx.close(); return
        needle = m.group(0)
        print(f"panel count phrase: {needle!r}\n")

        # Locate which cache entries contain this string
        hits = page.evaluate(LOCATE_JS, needle)
        print(f"cache entries containing this string: {len(hits)}")
        for h in hits:
            print(f"\n  KEY: {h['key'][:160]}")
            print(f"  ctx: {re.sub(chr(0x5b)+chr(0x22)+r'\\s+'+chr(0x5d), ' ', h['ctx'])[:400]!r}")

        # Find the exact field path in each hit
        print("\n=== exact field paths ===")
        for h in hits:
            paths = page.evaluate(PATH_JS, [h["key"], needle])
            for pinfo in paths:
                print(f"\n  KEY: {h['key'][:140]}")
                print(f"  PATH: {pinfo['path']}")
                print(f"  VALUE: {pinfo['value'][:150]!r}")

        with open("/tmp/probe_v8.json", "w") as f:
            json.dump({"needle": needle, "panel_head": panel[:600], "hits": hits}, f, indent=2, ensure_ascii=False)
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

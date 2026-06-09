"""Probe LinkedIn's in-memory Ember store to find applicant-count fields.

This is a one-shot diagnostic — not part of the extension. We launch Chrome
with the already-logged-in persistent profile, navigate to a job search page,
let it settle, then dump:

  1. Whether `window.Ember` is reachable.
  2. The shape of `_globalM3RecordDataCache`.
  3. Per-job `jobInsightsV2ResolutionResults` payloads (FrogHire's path).
  4. Any other store entries whose text mentions applicants.

If any of these surface a numeric applicant count, we've proved method B works.
"""
import json
import os
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=data%20engineer%20intern&geoId=103644278"

PROBE_JS = r"""
() => {
  const out = { errors: [], steps: [] };

  // ── Step 1: locate Ember service:store ────────────────────────────────
  try {
    const w = window;
    const Ember = w.requireModule ? w.requireModule("ember").default : w.Ember;
    if (!Ember) { out.errors.push("no window.Ember"); return out; }
    const app = Ember.Namespace.NAMESPACES.find(n => n instanceof Ember.Application);
    if (!app) { out.errors.push("no Ember Application in NAMESPACES"); return out; }
    const store = app.__container__.lookup("service:store");
    const cache = store && store._globalM3RecordDataCache;
    if (!cache) { out.errors.push("no _globalM3RecordDataCache on store"); return out; }
    out.steps.push("ember_store_found");

    // ── Step 2: enumerate keys ──────────────────────────────────────────
    const allKeys = Object.keys(cache);
    out.total_keys = allKeys.length;
    out.sample_keys = allKeys.slice(0, 8);

    const jobPostingKeys = allKeys.filter(k => k.includes("fsd_jobPosting:"));
    const jobCardKeys    = allKeys.filter(k => k.includes("fsd_jobPostingCard:"));
    out.jobPosting_count = jobPostingKeys.length;
    out.jobCard_count    = jobCardKeys.length;
    out.steps.push("keys_enumerated");

    // ── Step 3: dig insights for first few job cards ────────────────────
    const samples = [];
    for (const ck of jobCardKeys.slice(0, 5)) {
      const cardEntry = cache[ck];
      const cardData  = cardEntry && cardEntry.__data;
      if (!cardData) continue;
      const m = ck.match(/\(([^,]+),/);
      const jobId = m ? m[1] : null;
      const insights = (cardData.jobInsightsV2ResolutionResults || []).map(r => {
        const v = r && r.jobInsightViewModel;
        const desc = (v && v.description) || null;
        const text = Array.isArray(desc)
          ? desc.map(x => x && x.text && x.text.text).filter(Boolean).join(" | ")
          : (desc && desc.text && desc.text.text) || JSON.stringify(desc);
        return text;
      });
      // walk every nested field; flag anything whose key or string value mentions "applicant"
      const flatHits = [];
      try {
        const seen = new WeakSet();
        const walk = (o, path) => {
          if (!o || typeof o !== "object" || seen.has(o)) return;
          seen.add(o);
          if (path.length > 6) return;
          for (const [k, v] of Object.entries(o)) {
            if (typeof v === "string" && /applicant/i.test(v)) {
              flatHits.push([path.concat(k).join("."), v.slice(0, 120)]);
            } else if (typeof v === "number" && /applicant/i.test(k)) {
              flatHits.push([path.concat(k).join("."), v]);
            } else if (v && typeof v === "object") {
              walk(v, path.concat(k));
            }
          }
        };
        walk(cardData, [ck]);
      } catch (e) { flatHits.push(["__walkerr__", String(e)]); }

      samples.push({
        key: ck,
        jobId,
        insight_texts: insights,
        applicant_hits: flatHits.slice(0, 10),
      });
    }
    out.samples = samples;
    out.steps.push("samples_extracted");

    // ── Step 4: broad sweep — any cache entry mentioning applicants ─────
    const broadHits = [];
    let scanned = 0;
    for (const k of allKeys) {
      if (scanned++ > 4000) break;
      const e = cache[k];
      const d = e && e.__data;
      if (!d) continue;
      try {
        const s = JSON.stringify(d);
        if (/applicant/i.test(s)) {
          broadHits.push({ key: k, sample: s.slice(0, 200) });
          if (broadHits.length >= 10) break;
        }
      } catch {}
    }
    out.broad_hits = broadHits;
    out.steps.push("broad_swept");
  } catch (e) {
    out.errors.push("exception: " + (e && e.message));
    out.stack = e && e.stack;
  }
  return out;
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
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--password-store=basic",
            ],
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        print(f"→ navigating to {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(6_000)  # let Ember finish hydrating the list

        result = page.evaluate(PROBE_JS)
        out = "/tmp/probe_result.json"
        with open(out, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"→ full result: {out}")
        # echo a summary
        print(json.dumps({k: v for k, v in result.items() if k != "samples" and k != "broad_hits"}, indent=2, ensure_ascii=False))
        if result.get("samples"):
            print("--- first sample ---")
            print(json.dumps(result["samples"][0], indent=2, ensure_ascii=False)[:3000])
        if result.get("broad_hits"):
            print("--- broad hits ---")
            for h in result["broad_hits"][:5]:
                print(" ", h["key"])
                print("   ", h["sample"])

        page.screenshot(path="/usr/share/novnc/shots/probe.png", full_page=False)
        print("→ screenshot: http://localhost:6080/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

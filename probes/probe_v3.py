"""v3 fix: scroll the list panel more aggressively and dump raw card text
so we can verify the Promoted detection isn't a false positive."""
import json, os, re
from playwright.sync_api import sync_playwright

PROFILE = "/home/agent/.chrome-profile"
URL = "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&geoId=103644278&f_TPR=r86400"

DETAIL_TEXT_JS = "() => { const el = document.querySelector('.jobs-details') || document.querySelector('main'); return el ? el.innerText : null; }"

# Dump every card's RAW innerText so user can see what 'Promoted' detection catches
PICK_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll("[data-job-id]")) {
    const jid = el.getAttribute("data-job-id");
    if (!jid || seen.has(jid)) continue; seen.add(jid);
    const txt = el.innerText || "";
    out.push({ jobId: jid, raw: txt.slice(0, 300) });
  }
  return out;
}
"""

SCROLL_LIST_JS = r"""
async () => {
  // Find the actual scrollable list container.
  const list = document.querySelector(".scaffold-layout__list");
  const scroller = list ? (list.querySelector("ul")?.parentElement || list) : null;
  if (!scroller) return "no-scroller";
  for (let i = 0; i < 8; i++) {
    scroller.scrollBy(0, 1000);
    await new Promise(r => setTimeout(r, 800));
  }
  return "scrolled";
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

        scroll_status = page.evaluate(SCROLL_LIST_JS)
        print(f"scroll status: {scroll_status}")
        page.wait_for_timeout(2000)

        cards = page.evaluate(PICK_JS)
        print(f"total cards: {len(cards)}")
        # Tag promoted detection
        promoted_ids = []
        non_promoted_ids = []
        for c in cards:
            # Try several regex variants
            if re.search(r"\bpromoted\b", c["raw"], re.I):
                promoted_ids.append(c["jobId"])
            else:
                non_promoted_ids.append(c["jobId"])
        print(f"promoted: {len(promoted_ids)}  non-promoted: {len(non_promoted_ids)}")
        print("\n--- first 5 raw card texts (Promoted?) ---")
        for c in cards[:5]:
            is_p = re.search(r"\bpromoted\b", c["raw"], re.I) is not None
            print(f"\n  jobId={c['jobId']}  promoted={is_p}")
            for ln in c["raw"].split("\n"):
                if ln.strip(): print(f"    | {ln.strip()[:120]}")
        print("\n--- first 5 non-Promoted card texts ---")
        non_p = [c for c in cards if not re.search(r"\bpromoted\b", c["raw"], re.I)]
        for c in non_p[:5]:
            print(f"\n  jobId={c['jobId']}")
            for ln in c["raw"].split("\n"):
                if ln.strip(): print(f"    | {ln.strip()[:120]}")

        # Click 5 non-Promoted and dump right panel
        print("\n=== CLICKING 5 NON-PROMOTED CARDS ===")
        results = []
        for c in non_p[:5]:
            jid = c["jobId"]
            page.evaluate(r"""(jid) => {
              const root = document.querySelector(`[data-job-id="${jid}"]`);
              if (!root) return;
              root.scrollIntoView({block:"center"});
              const tgt = root.querySelector("a.job-card-list__title, a.job-card-container__link, .job-card-container--clickable") || root;
              tgt.click();
            }""", jid)
            page.wait_for_timeout(4500)
            detail = page.evaluate(DETAIL_TEXT_JS) or ""
            head_lines = [l.strip() for l in detail.split("\n") if l.strip()][:15]
            phrases = re.findall(
                r"\b(?:over\s+\d+|under\s+\d+|less than\s+\d+|\d+\+?)\s+[a-z]+(?:\s+[a-z]+){0,3}",
                detail[:800], re.I,
            )
            results.append({"jobId": jid, "detail_head_lines": head_lines, "metadata_phrases": phrases[:10]})
            print(f"\n══ {jid} (non-Promoted) ══")
            for ln in head_lines:
                print(f"    | {ln[:140]}")
            print(f"  metadata phrases: {phrases[:6]}")

        with open("/tmp/probe_v3.json", "w") as f:
            json.dump({"cards": cards, "results": results}, f, indent=2, ensure_ascii=False)
        page.screenshot(path="/usr/share/novnc/shots/probe.png")
        ctx.close()


if __name__ == "__main__":
    main()

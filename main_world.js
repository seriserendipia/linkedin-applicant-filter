/**
 * MAIN-world content script — runs in the LinkedIn page's own JS context
 * (not isolated), so it can read page-set JS (Ember store, React fiber props)
 * that the ISOLATED content script cannot.
 *
 * Two jobs:
 *  1. Classic board: read the Ember store's per-card tertiaryDescription
 *     (applicant text LinkedIn pre-loads for Promoted cards) and pipe it to
 *     the isolated script via window.postMessage — instant badges, no fetch.
 *  2. New AI/SDUI search (/jobs/search-results?origin=SEMANTIC...): there is
 *     NO Ember store and cards expose no data-job-id / href. The jobId lives
 *     in each card's React fiber props. We extract it and STAMP the card DOM
 *     element with data-jacf-jid="{id}". Attributes are real DOM (shared
 *     across worlds), so the isolated content script can then treat those
 *     elements as cards and reuse all its normal machinery.
 */
(function () {
  "use strict";

  function readEmberCache() {
    try {
      const w = window;
      const Ember = w.requireModule ? w.requireModule("ember").default : w.Ember;
      if (!Ember) return null;
      const app = Ember.Namespace.NAMESPACES.find((n) => n instanceof Ember.Application);
      if (!app) return null;
      const store = app.__container__.lookup("service:store");
      const cache = store && store._globalM3RecordDataCache;
      if (!cache) return null;

      const out = {};
      for (const k of Object.keys(cache)) {
        const m = k.match(/^urn:li:fsd_jobPostingCard:\((\d+),JOB_DETAILS\)$/);
        if (!m) continue;
        const jid = m[1];
        const d = cache[k] && cache[k].__data;
        const t = d && d.tertiaryDescription && d.tertiaryDescription.text;
        if (typeof t === "string" && t.length > 0) out[jid] = t;
      }
      return out;
    } catch {
      return null;
    }
  }

  let lastSerialized = "";
  function sweep() {
    const data = readEmberCache();
    if (!data) return;
    // Diff: only emit jids whose tertiary changed since last emit.
    const ser = JSON.stringify(data);
    if (ser === lastSerialized) return;
    lastSerialized = ser;
    window.postMessage({ __jacf: "ember_data", data }, "*");
  }

  // ── New AI/SDUI search: stamp card elements with their jobId ────────────
  const STAMP_ATTR = "data-jacf-jid";
  const ID_RES = [
    /JobCardFrameworkImpl\w*State_(\d{6,})/,
    /urn:li:fsd_jobPosting(?:Card)?:\(?(\d{6,})/,
    /\/jobs\/view\/(\d{6,})/,
  ];

  function fiberJobId(el) {
    const propsKey = Object.keys(el).find((k) => k.startsWith("__reactProps$"));
    const fiberKey = Object.keys(el).find(
      (k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$")
    );
    const seen = new Set();
    function find(obj, depth) {
      if (!obj || typeof obj !== "object" || seen.has(obj) || depth > 5) return null;
      seen.add(obj);
      for (const v of Object.values(obj)) {
        if (typeof v === "string") {
          for (const re of ID_RES) { const m = v.match(re); if (m) return m[1]; }
        } else if (v && typeof v === "object") {
          const r = find(v, depth + 1);
          if (r) return r;
        }
      }
      return null;
    }
    if (propsKey) { const r = find(el[propsKey], 0); if (r) return r; }
    if (fiberKey) {
      let f = el[fiberKey], hops = 0;
      while (f && hops < 4) {
        if (f.memoizedProps) { const r = find(f.memoizedProps, 0); if (r) return r; }
        f = f.return; hops++;
      }
    }
    return null;
  }

  function stampSemanticCards() {
    const root = document.querySelector('div[componentkey="SearchResultsMainContent"]');
    if (!root) return;
    // 1. For each jobId, find the SMALLEST element whose fiber resolves it
    //    (the most specific anchor node).
    const byId = new Map();
    for (const el of root.querySelectorAll("div")) {
      const id = fiberJobId(el);
      if (!id) continue;
      const size = el.getElementsByTagName("div").length;
      const cur = byId.get(id);
      if (!cur || size < cur.size) byId.set(id, { el, size });
    }
    // 2. Climb each anchor UP to the largest box that still belongs to ONLY
    //    this card (stop before a parent that also contains another card's
    //    anchor). That box is the real card unit — correct for both badge
    //    placement and filter (hide).
    const anchors = [...byId.entries()].map(([id, { el }]) => ({ id, el }));
    const anchorEls = anchors.map((a) => a.el);
    for (const a of anchors) {
      let card = a.el;
      while (card.parentElement && card.parentElement !== root) {
        const parent = card.parentElement;
        let mergesAnother = false;
        for (const other of anchorEls) {
          if (other !== a.el && parent.contains(other)) { mergesAnother = true; break; }
        }
        if (mergesAnother) break;
        card = parent;
      }
      if (card.getAttribute(STAMP_ATTR) !== a.id) card.setAttribute(STAMP_ATTR, a.id);
    }
  }

  // 1s polling matches the isolated content script's tick cadence.
  function tick() {
    sweep();              // classic board (Ember) — no-op on SDUI
    try { stampSemanticCards(); } catch { /* fiber shape changed; ignore */ }
  }
  setInterval(tick, 1000);
  setTimeout(tick, 600);  // also fire once shortly after document_idle
})();

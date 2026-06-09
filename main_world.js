/**
 * MAIN-world content script — runs in the LinkedIn page's own JS context
 * (not isolated). Reads the Ember store's per-card tertiaryDescription text
 * (where LinkedIn pre-loads applicant info for Promoted-by-hirer cards) and
 * pipes it back to the ISOLATED content script via window.postMessage.
 *
 * This is the FrogHire-style trick: ~25-30% of cards on a typical LinkedIn
 * search are Promoted and already have applicant info in the in-memory store.
 * For those, we skip the fetch entirely — instant badge.
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

  // 1s polling matches the isolated content script's tick cadence.
  // Short-circuit via lastSerialized makes idle ticks effectively free.
  setInterval(sweep, 1000);
  setTimeout(sweep, 600);  // also fire once shortly after document_idle
})();

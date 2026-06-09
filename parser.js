/**
 * Shared regex + bucket logic for LinkedIn applicant counts.
 *
 * Dual-export: this file is loaded both as a plain script (manifest content_scripts
 * + service worker importScripts) AND required from Node for unit tests. So we
 * attach to globalThis for the browser side and to module.exports for Node.
 *
 * The whole module is intentionally a pure function — no DOM, no fetch, no
 * Chrome APIs — so it's trivially unit-testable.
 */
(function () {
  "use strict";

  // Bucket layout per user spec: 0-10, 10-30, 30-50, 50-100, 100+ (left-inclusive).
  // We use canonical bucket IDs as keys; UI label can re-format.
  const BUCKETS = [
    { id: "0-10",   lo: 0,   hi: 10,        label: "0-10" },
    { id: "10-30",  lo: 10,  hi: 30,        label: "10-30" },
    { id: "30-50",  lo: 30,  hi: 50,        label: "30-50" },
    { id: "50-100", lo: 50,  hi: 100,       label: "50-100" },
    { id: "100+",   lo: 100, hi: Infinity,  label: "100+" },
  ];
  const UNKNOWN = "unknown";

  // Phrase patterns, ordered MOST specific first. The first match wins.
  // We anchor each pattern with the trailing noun phrase so things like
  // "404 people viewed this page" don't accidentally match.
  const PATTERNS = [
    // "Be among the first 25 applicants" / "Be among the first applicants"
    {
      regex: /be\s+among\s+the\s+first(?:\s+(\d{1,4}))?\s+applicants?/i,
      classify: (m) => ({
        count: m[1] ? Number(m[1]) : 10,  // no number → treat as "first ~10"
        kind: "firstN",
        raw: m[0],
      }),
    },
    // "Over 100 applicants" / "Over 100 people clicked apply"
    {
      regex: /over\s+(\d{1,4})\s+(?:applicants?|people\s+(?:clicked\s+apply|applied))/i,
      classify: (m) => ({ count: Number(m[1]), kind: "over", raw: m[0] }),
    },
    // "Under 25 applicants" / "Less than 10 applicants"
    {
      regex: /(?:under|less\s+than)\s+(\d{1,4})\s+applicants?/i,
      classify: (m) => ({
        count: Math.max(0, Number(m[1]) - 1),
        kind: "under",
        raw: m[0],
      }),
    },
    // Plain: "47 applicants" / "4 people clicked apply" / "23 people applied"
    {
      regex: /(?<![\w-])(\d{1,4})\s+(?:applicants?|people\s+(?:clicked\s+apply|applied))/i,
      classify: (m) => ({ count: Number(m[1]), kind: "exact", raw: m[0] }),
    },
  ];

  function parseApplicantCount(text) {
    if (!text || typeof text !== "string") return null;
    for (const p of PATTERNS) {
      const m = text.match(p.regex);
      if (m) return p.classify(m);
    }
    return null;
  }

  /**
   * Map a parsed result to a bucket id.
   *   - kind 'over' uses the *lower bound*, so "Over 100" → 100+ bucket.
   *   - kind 'firstN' uses the *upper bound* (because "first N" means count<=N).
   *   - kind 'exact' / 'under' use count directly.
   */
  function bucketFor(parsed) {
    if (!parsed) return UNKNOWN;
    let n = parsed.count;
    if (parsed.kind === "over") {
      // "Over 100" → put it in the 100+ bucket regardless of the precise
      // threshold LinkedIn used (it's always 100 in practice anyway).
      return "100+";
    }
    if (!Number.isFinite(n) || n < 0) return UNKNOWN;
    for (const b of BUCKETS) {
      if (n >= b.lo && n < b.hi) return b.id;
    }
    return "100+";
  }

  const api = { parseApplicantCount, bucketFor, BUCKETS, UNKNOWN };

  // Dual export
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.JACFParser = api;
  }
})();

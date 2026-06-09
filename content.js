/**
 * Content script (ISOLATED world):
 *   - Polls the page for job cards (.scaffold-layout__list-item with [data-job-id]).
 *   - Sends new jobIds to background.js.
 *   - Receives parsed results back, paints a bucket badge on each card,
 *     and applies the user's filter (hide cards whose bucket isn't checked).
 *   - Injects a single-line filter bar above the list with: bucket toggles,
 *     a clear button, and a collapse button.
 *   - UI prefs (which buckets are checked + collapsed state) persist to
 *     chrome.storage.local so they survive page reloads and browser restarts.
 *
 * Polling instead of MutationObserver because LinkedIn's list churns
 * continuously (virtualization, observer debounce can starve). 1s ticks are
 * cheap thanks to the seen-set short-circuit.
 */
(function () {
  "use strict";

  const BADGE_CLASS = "__jacf-badge";
  const BAR_ID = "__jacf-filter-bar";
  const UI_PREFS_KEY = "__jacf_ui";

  // Bucket UI labels (left-inclusive ranges). The 'unknown' bucket uses '?'
  // to save horizontal space; full label is in title attribute.
  const BUCKET_DEFS = [
    { id: "0-10",    label: "0-10",   color: "#0a7d39" },  // green = low competition
    { id: "10-30",   label: "10-30",  color: "#3a8a44" },
    { id: "30-50",   label: "30-50",  color: "#a47a18" },
    { id: "50-100",  label: "50-100", color: "#b8541c" },
    { id: "100+",    label: "100+",   color: "#a52828" },  // red = high competition
    { id: "unknown", label: "?",      color: "#666"    },
  ];

  // ── State ──────────────────────────────────────────────────────────────
  const sentJobIds = new Set();             // jobIds we've already pushed to background
  const results = new Map();                // jid → { bucket, count, kind, raw, error? }
  let activeBuckets = new Set();            // empty → show all
  let collapsed = false;                    // bar hidden when true
  let prefsLoaded = false;                  // wait for storage before painting UI
  let stopBannerShown = false;
  let lastSearchKey = "";

  // ── Persist UI prefs ───────────────────────────────────────────────────
  function loadPrefs() {
    chrome.storage.local.get(UI_PREFS_KEY, (got) => {
      const p = (got && got[UI_PREFS_KEY]) || {};
      activeBuckets = new Set(Array.isArray(p.activeBuckets) ? p.activeBuckets : []);
      collapsed = !!p.collapsed;
      prefsLoaded = true;
    });
  }
  function savePrefs() {
    chrome.storage.local.set({
      [UI_PREFS_KEY]: {
        activeBuckets: Array.from(activeBuckets),
        collapsed,
      },
    });
  }
  loadPrefs();

  // ── Bridge for tests: page world ↔ content script ──────────────────────
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.__jacf !== "request") return;
    if (msg.kind === "get_state") {
      window.postMessage({
        __jacf: "response",
        requestId: msg.requestId,
        state: {
          resultsInMemory: Array.from(results.entries()),
          sentJobIds: Array.from(sentJobIds),
          activeBuckets: Array.from(activeBuckets),
          collapsed,
          prefsLoaded,
        },
      }, "*");
    }
  });

  // Ember main-world shortcut: forward raw tertiary strings to the SW, which
  // parses + caches them and pushes back via JACF_RESULT. For Promoted cards
  // this skips the fetch queue entirely → instant badge.
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.__jacf !== "ember_data") return;
    // Only forward jids we haven't already resolved
    const filtered = {};
    for (const [jid, t] of Object.entries(msg.data || {})) {
      if (results.has(jid)) continue;
      filtered[jid] = t;
    }
    if (Object.keys(filtered).length === 0) return;
    chrome.runtime.sendMessage({ type: "JACF_EMBER_RESULT", data: filtered }, () => {
      // ignore errors — content script has nothing to do on failure
    });
  });

  // ── Messages from background (results + toolbar toggle) ────────────────
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || !msg.type) return;
    if (msg.type === "JACF_RESULT") {
      results.set(msg.jid, msg.result);
      paintBadgeForJob(msg.jid);
      applyFilter();
      if (msg.stopped && !stopBannerShown) {
        showStopBanner(msg.stopReason || "stopped");
      }
    } else if (msg.type === "JACF_TOGGLE_BAR") {
      collapsed = !collapsed;
      savePrefs();
      applyCollapsedState();
    }
  });

  // ── Main loop ──────────────────────────────────────────────────────────
  setInterval(tick, 1000);
  setTimeout(tick, 500);

  function tick() {
    if (!/\/jobs\/(?:search|collections|search-results)/.test(location.pathname)) return;
    if (!prefsLoaded) return;   // wait until we've restored persisted state

    const searchKey = location.pathname + location.search.replace(/&currentJobId=\d+/, "");
    if (searchKey !== lastSearchKey) {
      lastSearchKey = searchKey;
      sentJobIds.clear();
    }

    ensureFilterBar();

    const newIds = [];
    for (const card of collectCards()) {
      const jid = card.getAttribute("data-job-id")
                || card.querySelector("[data-job-id]")?.getAttribute("data-job-id");
      if (!jid) continue;
      // Only paint the final bucket badge on the card. Loading/pending
      // feedback lives in our filter bar so we don't visually trespass
      // on LinkedIn's UI.
      if (results.has(jid)) paintBadgeForJob(jid, card);
      if (sentJobIds.has(jid)) continue;
      sentJobIds.add(jid);
      newIds.push(jid);
    }

    if (newIds.length > 0) {
      chrome.runtime.sendMessage(
        { type: "JACF_ENQUEUE", jobIds: newIds },
        (resp) => {
          if (chrome.runtime.lastError || !resp) return;
          if (resp.cached) {
            for (const [jid, val] of Object.entries(resp.cached)) {
              results.set(jid, val);
              paintBadgeForJob(jid);
            }
            applyFilter();
          }
          if (resp.stopped && !stopBannerShown) showStopBanner(resp.stopReason || "stopped");
          updateProgress(resp);
        }
      );
    }

    applyFilter();
    updateProgress(null);   // refresh ETA every tick from in-memory state
  }

  // ── DOM helpers ────────────────────────────────────────────────────────
  function collectCards() {
    const selectors = [
      ".scaffold-layout__list-item",
      ".jobs-search-results__list-item",
      "li[data-occludable-job-id]",
    ];
    const seen = new Set();
    const out = [];
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (seen.has(el)) continue;
        seen.add(el);
        out.push(el);
      }
    }
    return out;
  }

  function findCardForJobId(jid) {
    return (
      document.querySelector(`li[data-occludable-job-id="${jid}"]`) ||
      document.querySelector(`[data-job-id="${jid}"]`)?.closest("li, .scaffold-layout__list-item") ||
      document.querySelector(`[data-job-id="${jid}"]`)
    );
  }

  function getOrCreateBadge(card) {
    let badge = card.querySelector(`.${BADGE_CLASS}`);
    if (!badge) {
      badge = document.createElement("span");
      badge.className = BADGE_CLASS;
      if (getComputedStyle(card).position === "static") card.style.position = "relative";
      card.appendChild(badge);
    }
    return badge;
  }

  function paintBadgeForJob(jid, card) {
    const res = results.get(jid);
    if (!res) return;
    card = card || findCardForJobId(jid);
    if (!card) return;

    const badge = getOrCreateBadge(card);
    const bucketId = res.bucket || "unknown";
    const def = BUCKET_DEFS.find((b) => b.id === bucketId) || BUCKET_DEFS[BUCKET_DEFS.length - 1];
    badge.style.background = def.color;
    badge.textContent = res.error ? "?" : def.label;
    badge.title = res.error
      ? `error: ${res.error}`
      : (res.raw ? `LinkedIn says: "${res.raw}"` : "no count text found");
    card.setAttribute("data-jacf-bucket", bucketId);
  }

  function applyFilter() {
    if (activeBuckets.size === 0) {
      for (const el of document.querySelectorAll("[data-jacf-hidden]")) {
        el.style.display = "";
        el.removeAttribute("data-jacf-hidden");
      }
      return;
    }
    for (const card of collectCards()) {
      const bucket = card.getAttribute("data-jacf-bucket");
      if (!bucket) continue;  // no bucket yet → leave alone (still loading)
      const shouldShow = activeBuckets.has(bucket);
      if (shouldShow && card.hasAttribute("data-jacf-hidden")) {
        card.style.display = "";
        card.removeAttribute("data-jacf-hidden");
      } else if (!shouldShow && !card.hasAttribute("data-jacf-hidden")) {
        card.style.display = "none";
        card.setAttribute("data-jacf-hidden", "true");
      }
    }
  }

  // ── Filter bar UI ──────────────────────────────────────────────────────
  function ensureFilterBar() {
    if (document.getElementById(BAR_ID)) return;
    const list = document.querySelector(".scaffold-layout__list");
    if (!list) return;

    const bar = document.createElement("div");
    bar.id = BAR_ID;
    // Inline SVG icons — keep them simple, monochrome currentColor so they
    // pick up the button's text color.
    const ICON_CLEAR = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M2 3h12l-4.5 5.2V13L6.5 11.5V8.2L2 3z"/><path d="M14 2L2 14"/>
    </svg>`;
    const ICON_COLLAPSE = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M3 3h10"/><path d="M8 14V7"/><path d="M5 10l3-3 3 3"/>
    </svg>`;
    bar.innerHTML = `
      <span class="__jacf-title">人数</span>
      <div class="__jacf-checks"></div>
      <button class="__jacf-btn __jacf-clear" type="button" title="清除全部勾选" aria-label="清除全部勾选">${ICON_CLEAR}</button>
      <span class="__jacf-progress" id="__jacf-progress">扫描中…</span>
      <button class="__jacf-btn __jacf-collapse" type="button" title="收起(点扩展图标恢复)" aria-label="收起">${ICON_COLLAPSE}</button>
    `;
    const checks = bar.querySelector(".__jacf-checks");
    for (const def of BUCKET_DEFS) {
      const wrap = document.createElement("label");
      wrap.className = "__jacf-check";
      // Per-pill --c lets CSS use the bucket color for the checked fill state
      // (background swap + white label) without duplicating colors in CSS.
      wrap.style.setProperty("--c", def.color);
      wrap.innerHTML = `
        <input type="checkbox" data-bucket="${def.id}" ${activeBuckets.has(def.id) ? "checked" : ""}>
        <span class="__jacf-swatch" style="background:${def.color}"></span>
        <span class="__jacf-label">${def.label}</span>
        <span class="__jacf-count" data-count-for="${def.id}">0</span>
      `;
      wrap.querySelector("input").addEventListener("change", (e) => {
        const bid = e.target.getAttribute("data-bucket");
        if (e.target.checked) activeBuckets.add(bid); else activeBuckets.delete(bid);
        savePrefs();
        applyFilter();
      });
      checks.appendChild(wrap);
    }
    bar.querySelector(".__jacf-clear").addEventListener("click", clearAll);
    bar.querySelector(".__jacf-collapse").addEventListener("click", () => {
      collapsed = true;
      savePrefs();
      applyCollapsedState();
    });

    list.insertBefore(bar, list.firstChild);
    applyCollapsedState();
    setInterval(updateBucketCounts, 1000);
  }

  function clearAll() {
    activeBuckets.clear();
    for (const cb of document.querySelectorAll(`#${BAR_ID} input[type=checkbox]`)) cb.checked = false;
    savePrefs();
    applyFilter();
  }

  function applyCollapsedState() {
    const bar = document.getElementById(BAR_ID);
    if (!bar) return;
    bar.classList.toggle("__jacf-hidden", collapsed);
  }

  function updateBucketCounts() {
    const tally = {};
    for (const def of BUCKET_DEFS) tally[def.id] = 0;
    for (const v of results.values()) tally[(v && v.bucket) || "unknown"]++;
    for (const def of BUCKET_DEFS) {
      const el = document.querySelector(`[data-count-for="${def.id}"]`);
      if (el) el.textContent = String(tally[def.id]);
    }
  }

  // Track last-known SW stats so we can update progress every tick, not just
  // when we send an ENQUEUE.
  let lastSwStats = { activeWorkers: 3, queueSize: 0 };

  function updateProgress(resp) {
    if (resp) {
      lastSwStats = {
        activeWorkers: resp.activeWorkers || lastSwStats.activeWorkers,
        queueSize:     resp.queueSize     ?? lastSwStats.queueSize,
      };
    }
    const el = document.getElementById("__jacf-progress");
    const bar = document.getElementById(BAR_ID);
    if (!el || !bar) return;
    const total = sentJobIds.size;
    const done = results.size;
    const remaining = Math.max(0, total - done);
    const workers = Math.max(1, lastSwStats.activeWorkers || 1);
    // ETA = remaining * (avg jitter / workers). avg jitter ≈ 2.5s.
    const etaSec = Math.round((remaining * 2.5) / workers);
    if (remaining > 0) {
      el.textContent = `已标 ${done}/${total} · 约 ${etaSec}s`;
      el.style.display = "";
    } else {
      // When idle, hide the progress text entirely so the bar stays tight.
      // The slim bottom progress line already signals "done" by fading out.
      el.style.display = "none";
    }
    // Drive the slim progress bar at the bottom of OUR filter bar — the only
    // place we paint loading feedback. Clamp to 100% (results.size can briefly
    // exceed sentJobIds.size when the Ember shortcut hands back jids we
    // haven't yet seen in the visible card scan).
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    bar.style.setProperty("--jacf-progress", pct + "%");
    bar.classList.toggle("__jacf-loading", remaining > 0);
  }

  function showStopBanner(reason) {
    stopBannerShown = true;
    const el = document.getElementById("__jacf-progress");
    if (!el) return;
    const labels = {
      rate_limited_429: "⚠️429 限流",
      linkedin_anti_bot_999: "⚠️反爬 999",
      session_cap: "ℹ️达上限",
    };
    el.textContent = labels[reason] || `停: ${reason}`;
    el.style.color = "#c33";
    el.style.fontWeight = "600";
  }
})();

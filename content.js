/**
 * Content script (ISOLATED world):
 *   - Polls the page for job cards, sends new jobIds to background.js.
 *   - Receives parsed results, paints a bucket badge per card, applies filter.
 *   - Injects a single-line filter bar above the list.
 *   - UI prefs persist to chrome.storage.local.
 *
 * Resilience (added after LinkedIn shipped a new SDUI layout to some accounts):
 *   - extAlive() guard: when the extension context is invalidated (e.g. after
 *     a reload), every chrome.* call throws. We detect that and STOP all
 *     polling so orphaned scripts don't spin forever.
 *   - Layout detection + give-up: if we can't recognize the page layout after
 *     GIVE_UP_TICKS, we show a one-time fallback notice and drop to a slow
 *     heartbeat instead of hammering querySelectorAll every second.
 *   - We never mutate LinkedIn's DOM on an unrecognized layout (no half-broken
 *     badges); the user gets an honest "LinkedIn changed, update needed" note.
 */
(function () {
  "use strict";

  const BADGE_CLASS = "__jacf-badge";
  const BAR_ID = "__jacf-filter-bar";
  const NOTICE_ID = "__jacf-notice";
  const UI_PREFS_KEY = "__jacf_ui";

  const TICK_MS = 1000;          // normal poll cadence
  const HEARTBEAT_MS = 8000;     // slow cadence after we give up on the layout
  const GIVE_UP_TICKS = 12;      // ~12s of "layout unrecognized" before fallback

  const BUCKET_DEFS = [
    { id: "0-10",    label: "0-10",   color: "#0a7d39" },
    { id: "10-30",   label: "10-30",  color: "#3a8a44" },
    { id: "30-50",   label: "30-50",  color: "#a47a18" },
    { id: "50-100",  label: "50-100", color: "#b8541c" },
    { id: "100+",    label: "100+",   color: "#a52828" },
    { id: "unknown", label: "?",      color: "#666"    },
  ];

  // ── State ──────────────────────────────────────────────────────────────
  const sentJobIds = new Set();
  const results = new Map();
  let activeBuckets = new Set();
  let collapsed = false;
  let prefsLoaded = false;
  let stopBannerShown = false;
  let lastSearchKey = "";
  let unrecognizedTicks = 0;
  let gaveUp = false;
  let tickTimer = null;
  let countsTimer = null;
  let dead = false;

  // ── Extension-context liveness ──────────────────────────────────────────
  // After an extension reload/update, content scripts already injected into
  // open tabs become "orphaned": chrome.runtime.id goes undefined and every
  // chrome.* call throws. Detect and shut down cleanly.
  function extAlive() {
    try { return !!(chrome && chrome.runtime && chrome.runtime.id); }
    catch { return false; }
  }
  function shutdown(reason) {
    if (dead) return;
    dead = true;
    if (tickTimer) clearTimeout(tickTimer);
    if (countsTimer) clearInterval(countsTimer);
    // Best-effort: leave any rendered bar in place; just stop doing work.
    // (No console spam — this is an expected lifecycle event.)
  }

  // ── Persist UI prefs ───────────────────────────────────────────────────
  function loadPrefs() {
    if (!extAlive()) return;
    try {
      chrome.storage.local.get(UI_PREFS_KEY, (got) => {
        if (chrome.runtime.lastError) return;
        const p = (got && got[UI_PREFS_KEY]) || {};
        activeBuckets = new Set(Array.isArray(p.activeBuckets) ? p.activeBuckets : []);
        collapsed = !!p.collapsed;
        prefsLoaded = true;
      });
    } catch { /* context died between check and call */ }
  }
  function savePrefs() {
    if (!extAlive()) return;
    try {
      chrome.storage.local.set({
        [UI_PREFS_KEY]: { activeBuckets: Array.from(activeBuckets), collapsed },
      });
    } catch { /* ignore */ }
  }
  loadPrefs();

  // ── Bridge for tests ────────────────────────────────────────────────────
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
          collapsed, prefsLoaded, gaveUp,
        },
      }, "*");
    }
  });

  // ── Ember main-world shortcut ──────────────────────────────────────────
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.__jacf !== "ember_data") return;
    if (!extAlive()) { shutdown("ember-orphan"); return; }
    const filtered = {};
    for (const [jid, t] of Object.entries(msg.data || {})) {
      if (results.has(jid)) continue;
      filtered[jid] = t;
    }
    if (Object.keys(filtered).length === 0) return;
    try {
      chrome.runtime.sendMessage({ type: "JACF_EMBER_RESULT", data: filtered }, () => {
        void chrome.runtime.lastError;  // swallow
      });
    } catch { shutdown("ember-send"); }
  });

  // ── Messages from background ───────────────────────────────────────────
  try {
    chrome.runtime.onMessage.addListener((msg) => {
      if (!msg || !msg.type) return;
      if (msg.type === "JACF_RESULT") {
        results.set(msg.jid, msg.result);
        paintBadgeForJob(msg.jid);
        applyFilter();
        if (msg.stopped && !stopBannerShown) showStopBanner(msg.stopReason || "stopped");
      } else if (msg.type === "JACF_TOGGLE_BAR") {
        collapsed = !collapsed;
        savePrefs();
        applyCollapsedState();
      }
    });
  } catch { /* context already dead */ }

  // ── Main loop (self-scheduling so we can vary the cadence) ─────────────
  function scheduleTick(delay) {
    if (dead) return;
    tickTimer = setTimeout(runTick, delay);
  }
  function runTick() {
    if (dead) return;
    if (!extAlive()) { shutdown("tick-orphan"); return; }
    let nextDelay = TICK_MS;
    try {
      nextDelay = tick();
    } catch (e) {
      // Never let one bad tick kill the loop — but if it's a context error, stop.
      if (!extAlive()) { shutdown("tick-throw"); return; }
    }
    scheduleTick(nextDelay || TICK_MS);
  }
  scheduleTick(500);

  // Returns the delay (ms) until the next tick.
  function tick() {
    if (!/\/jobs\/(?:search|collections|search-results)/.test(location.pathname)) {
      // Not on a job list page — clear any fallback notice, idle slowly.
      removeNotice();
      return HEARTBEAT_MS;
    }
    if (!prefsLoaded) return TICK_MS;

    const searchKey = location.pathname + location.search.replace(/&currentJobId=\d+/, "");
    if (searchKey !== lastSearchKey) {
      lastSearchKey = searchKey;
      sentJobIds.clear();
      unrecognizedTicks = 0;
      gaveUp = false;
      removeNotice();
    }

    const container = findListContainer();
    const cards = container ? collectCards() : [];

    // ── Layout recognition gate ──────────────────────────────────────────
    if (!container || cards.length === 0) {
      // Is there a *new-layout* signal on the page? If so and we still can't
      // read it, this is the unsupported SDUI layout — give up gracefully.
      const newLayoutSignal =
        document.querySelector('div[componentkey="SearchResultsMainContent"]') ||
        document.querySelector("div[data-sdui-screen]") ||
        document.querySelector('a[href*="/jobs/view/"]');
      unrecognizedTicks++;
      if (newLayoutSignal && unrecognizedTicks >= GIVE_UP_TICKS && !gaveUp) {
        gaveUp = true;
        showFallbackNotice();
        return HEARTBEAT_MS;       // stop hammering; recover if page changes
      }
      return gaveUp ? HEARTBEAT_MS : TICK_MS;
    }

    // We recognized the layout — clear any stale fallback + reset counter.
    unrecognizedTicks = 0;
    if (gaveUp) { gaveUp = false; removeNotice(); }

    ensureFilterBar(container);

    const newIds = [];
    for (const card of cards) {
      const jid = jobIdOf(card);
      if (!jid) continue;
      if (results.has(jid)) paintBadgeForJob(jid, card);
      if (sentJobIds.has(jid)) continue;
      sentJobIds.add(jid);
      newIds.push(jid);
    }

    if (newIds.length > 0) {
      try {
        chrome.runtime.sendMessage({ type: "JACF_ENQUEUE", jobIds: newIds }, (resp) => {
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
        });
      } catch { shutdown("enqueue-send"); return TICK_MS; }
    }

    applyFilter();
    updateProgress(null);
    return TICK_MS;
  }

  // ── Layout abstraction (old + new SDUI) ────────────────────────────────
  // OLD board: .scaffold-layout__list with .scaffold-layout__list-item cards
  //   (data-job-id present).
  // NEW AI/SDUI search: no scaffold list, no data-job-id. main_world.js stamps
  //   each card with data-jacf-jid="{id}" (extracted from React fiber). The
  //   container is div[componentkey="SearchResultsMainContent"].
  // We only treat a layout as "recognized" when we can find a container AND
  // collect cards with extractable job IDs.
  function findListContainer() {
    return document.querySelector(".scaffold-layout__list")
        || document.querySelector('div[componentkey="SearchResultsMainContent"]')
        || null;
  }

  function collectCards() {
    const selectors = [
      ".scaffold-layout__list-item",
      ".jobs-search-results__list-item",
      "li[data-occludable-job-id]",
      "[data-jacf-jid]",                  // new SDUI: stamped by main_world.js
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

  function jobIdOf(card) {
    return card.getAttribute("data-jacf-jid")
        || card.getAttribute("data-job-id")
        || card.getAttribute("data-occludable-job-id")
        || card.querySelector("[data-job-id]")?.getAttribute("data-job-id")
        || (card.querySelector('a[href*="/jobs/view/"]')?.getAttribute("href")
              ?.match(/\/jobs\/view\/(\d+)/)?.[1])
        || null;
  }

  function findCardForJobId(jid) {
    return (
      document.querySelector(`[data-jacf-jid="${jid}"]`) ||
      document.querySelector(`li[data-occludable-job-id="${jid}"]`) ||
      document.querySelector(`[data-job-id="${jid}"]`)?.closest("li, .scaffold-layout__list-item") ||
      document.querySelector(`[data-job-id="${jid}"]`)
    );
  }

  // ── Badges ──────────────────────────────────────────────────────────────
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
    badge.title = res.error ? `error: ${res.error}`
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
      if (!bucket) continue;
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
  function ensureFilterBar(container) {
    if (document.getElementById(BAR_ID)) return;
    const list = container || findListContainer();
    if (!list) return;

    const bar = document.createElement("div");
    bar.id = BAR_ID;
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

    // Old scaffold list: insert the bar inside, above the <ul>. SDUI container:
    // insert before it (as a previous sibling) so we don't disturb its
    // internal scroll/virtualization.
    if (list.classList.contains("scaffold-layout__list")) {
      list.insertBefore(bar, list.firstChild);
    } else if (list.parentElement) {
      list.parentElement.insertBefore(bar, list);
    } else {
      list.insertBefore(bar, list.firstChild);
    }
    applyCollapsedState();
    if (!countsTimer) countsTimer = setInterval(() => {
      if (!extAlive()) { shutdown("counts-orphan"); return; }
      updateBucketCounts();
    }, 1000);
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
    const etaSec = Math.round((remaining * 2.5) / workers);
    if (remaining > 0) {
      el.textContent = `已标 ${done}/${total} · 约 ${etaSec}s`;
      el.style.display = "";
    } else {
      el.style.display = "none";
    }
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

  // ── Fallback notice for unsupported (new SDUI) layout ───────────────────
  function showFallbackNotice() {
    if (document.getElementById(NOTICE_ID)) return;
    const n = document.createElement("div");
    n.id = NOTICE_ID;
    n.innerHTML = `
      <span class="__jacf-notice-text">领英更新了职位页面布局,本扩展暂时无法在此页面读取申请人数,正在适配中。</span>
      <button class="__jacf-notice-close" type="button" aria-label="关闭">×</button>
    `;
    n.querySelector(".__jacf-notice-close").addEventListener("click", () => n.remove());
    document.body.appendChild(n);
  }
  function removeNotice() {
    const n = document.getElementById(NOTICE_ID);
    if (n) n.remove();
  }
})();

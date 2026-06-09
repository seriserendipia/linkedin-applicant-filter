/**
 * Background service worker:
 *   - Maintains a queue of jobIds to fetch.
 *   - N concurrent workers pull from the queue, each waits its own jitter
 *     (1.5-3.5s) between requests. Total throughput ≈ N / 2.5s.
 *   - Adaptive backoff: on 429/999 we drop to 1 worker and stop accepting new
 *     items for a cooldown window.
 *   - Each fetch hits /jobs/view/${jid}/, regex-extracts the applicant phrase
 *     from the SSR HTML, classifies into a bucket.
 *   - Also accepts pre-parsed results from the Ember main-world shortcut
 *     (JACF_EMBER_RESULT) and short-circuits the fetch for those jids.
 *   - Caches in chrome.storage.session (lives until browser closes — per user
 *     requirement, not permanent).
 *   - Hard caps at MAX_PER_SESSION to protect the user's account.
 */
importScripts("parser.js");

const MAX_PER_SESSION = 100;
const FETCH_TIMEOUT_MS = 12000;

// Default workload: 3 workers × ~2.5s jitter ≈ 1.2 req/s aggregate.
// On rate-limit we collapse to 1 worker with much longer jitter.
const NORMAL = { concurrency: 3, minDelay: 1500, maxDelay: 3500 };
const BACKOFF = { concurrency: 1, minDelay: 8000, maxDelay: 15000 };
let mode = NORMAL;

const queue = [];                          // [{ jid, tabId }]
const pending = new Set();                 // jids currently in queue
let activeWorkers = 0;                     // currently-running worker promises
let stopped = false;
let stopReason = "";
let processedCount = 0;

// In-memory cache. We also write to storage.session for survival across SW restarts.
const cache = new Map();                   // jid → result

// chrome.storage.session defaults to TRUSTED_CONTEXTS only (background/popup,
// NOT content scripts). Open it up so our content script can read it for the
// in-page bridge. setAccessLevel is idempotent and survives SW restarts.
chrome.storage.session
  .setAccessLevel({ accessLevel: "TRUSTED_AND_UNTRUSTED_CONTEXTS" })
  .catch((e) => console.warn("[JACF] setAccessLevel failed:", e));

// Diagnostic globals (used by tests via sw.evaluate).
self.__jacfStats = () => ({
  queueSize: queue.length,
  processing: activeWorkers > 0,
  activeWorkers,
  processedCount,
  cacheSize: cache.size,
  stopped,
  stopReason,
  mode: mode === NORMAL ? "normal" : "backoff",
});

chrome.action.onClicked.addListener((tab) => {
  if (!tab || tab.id == null) return;
  chrome.tabs.sendMessage(tab.id, { type: "JACF_TOGGLE_BAR" }).catch(() => {});
});

chrome.storage.session.get(null).then((all) => {
  for (const [k, v] of Object.entries(all)) {
    if (k.startsWith("jacf_") && v) cache.set(k.slice(5), v);
  }
});

// ── Message handler ────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return false;
  const tabId = sender.tab && sender.tab.id;

  if (msg.type === "JACF_ENQUEUE") {
    const knownNow = {};
    let newlyQueued = 0;
    for (const jid of msg.jobIds || []) {
      if (cache.has(jid)) { knownNow[jid] = cache.get(jid); continue; }
      if (pending.has(jid)) continue;
      pending.add(jid);
      queue.push({ jid, tabId });
      newlyQueued++;
    }
    sendResponse({
      ok: true, stopped, stopReason,
      cached: knownNow,
      queued: newlyQueued,
      queueSize: queue.length,
      processedCount,
      max: MAX_PER_SESSION,
      activeWorkers,
    });
    if (!stopped) ensureWorkers();
    return true;
  }

  // Main-world Ember shortcut → these results never hit the network.
  if (msg.type === "JACF_EMBER_RESULT") {
    const fresh = [];
    for (const [jid, text] of Object.entries(msg.data || {})) {
      if (cache.has(jid)) continue;
      const parsed = JACFParser.parseApplicantCount(text);
      if (!parsed) continue;
      const result = { ...parsed, bucket: JACFParser.bucketFor(parsed), source: "ember" };
      cache.set(jid, result);
      chrome.storage.session.set({ [`jacf_${jid}`]: result }).catch(() => {});
      // If a fetch was already queued for this jid, drop it.
      if (pending.has(jid)) {
        pending.delete(jid);
        const i = queue.findIndex((q) => q.jid === jid);
        if (i >= 0) queue.splice(i, 1);
      }
      fresh.push({ jid, result });
    }
    sendResponse({ ok: true, fresh: fresh.length });
    if (tabId != null) {
      for (const { jid, result } of fresh) {
        chrome.tabs.sendMessage(tabId, {
          type: "JACF_RESULT", jid, result,
          processedCount, max: MAX_PER_SESSION, stopped, stopReason,
        }).catch(() => {});
      }
    }
    return true;
  }

  if (msg.type === "JACF_GET_STATE") {
    sendResponse({
      stopped, stopReason, processedCount,
      queueSize: queue.length, max: MAX_PER_SESSION, cacheSize: cache.size,
      activeWorkers,
    });
    return true;
  }

  if (msg.type === "JACF_RESET") {
    queue.length = 0;
    pending.clear();
    stopped = false;
    stopReason = "";
    mode = NORMAL;
    sendResponse({ ok: true });
    return true;
  }

  return false;
});

// ── Worker pool ────────────────────────────────────────────────────────────
function ensureWorkers() {
  if (stopped) return;
  while (activeWorkers < mode.concurrency && queue.length > 0) {
    activeWorkers++;
    worker().finally(() => { activeWorkers--; });
  }
}

async function worker() {
  while (!stopped && queue.length > 0) {
    if (processedCount >= MAX_PER_SESSION) {
      stopped = true;
      stopReason = "session_cap";
      break;
    }
    const { jid, tabId } = queue.shift();
    pending.delete(jid);

    let result;
    try { result = await fetchOne(jid); }
    catch (e) { result = { error: "exception:" + (e && e.message) }; }
    processedCount++;

    cache.set(jid, result);
    chrome.storage.session.set({ [`jacf_${jid}`]: result }).catch(() => {});

    if (tabId != null) {
      chrome.tabs.sendMessage(tabId, {
        type: "JACF_RESULT", jid, result,
        processedCount, max: MAX_PER_SESSION, stopped, stopReason,
      }).catch(() => {});
    }
    if (stopped) break;
    // Per-worker jitter — no shared barrier, each worker paces itself.
    const delay = mode.minDelay + Math.random() * (mode.maxDelay - mode.minDelay);
    await new Promise((r) => setTimeout(r, delay));
  }
}

// ── The actual fetch ───────────────────────────────────────────────────────
async function fetchOne(jid) {
  const url = `https://www.linkedin.com/jobs/view/${jid}/`;
  const ctrl = new AbortController();
  const tmo = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  let resp;
  try {
    resp = await fetch(url, {
      credentials: "include",
      signal: ctrl.signal,
      headers: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });
  } catch (e) {
    return { error: "fetch_failed:" + (e && e.message) };
  } finally {
    clearTimeout(tmo);
  }

  if (resp.status === 429) { triggerBackoff("rate_limited_429"); return { error: "rate_limited" }; }
  if (resp.status === 999) { triggerBackoff("linkedin_anti_bot_999"); return { error: "linkedin_999" }; }
  if (resp.status !== 200)  { return { error: "status_" + resp.status }; }

  const html = await resp.text();
  const parsed = JACFParser.parseApplicantCount(html);
  const bucket = JACFParser.bucketFor(parsed);
  return { ...(parsed || {}), bucket, source: "fetch" };
}

function triggerBackoff(reason) {
  // Soft backoff: keep going but at 1 worker + much longer jitter.
  // Only HARD stop if we hit two backoffs back-to-back.
  if (mode === BACKOFF) {
    stopped = true;
    stopReason = reason + "_persisted";
    return;
  }
  mode = BACKOFF;
  stopReason = reason;
  console.warn("[JACF-BG] backoff mode:", reason);
}

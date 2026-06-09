# Chrome Web Store submission package

Everything you need to paste into the Chrome Web Store developer dashboard.

---

## 1. Short description (≤ 132 chars)

> Filter LinkedIn job results by precise applicant-count buckets (0-10, 10-30, 30-50, 50-100, 100+) — find low-competition jobs.

(131 chars.)

---

## 2. Detailed description

```
Find jobs you can actually win.

LinkedIn shows you 36,000 results for "Software Engineer" and gives you ONE
filter for competition: a checkbox labeled "Has under 10 applicants". That's
it. Every other job — 11, 30, 90, "Over 100" — is collapsed into one
undifferentiated pile.

This extension fixes that. The moment you land on a LinkedIn job search, a
small bar appears above the results with six bucket checkboxes:

    [● 0-10]  [● 10-30]  [● 30-50]  [● 50-100]  [● 100+]  [● unknown]

Every job card gets a colored badge in its corner showing its bucket — green
for low competition, red for high. Check the buckets you care about and the
list hides everything else.

WHY IT'S USEFUL
• Spend your application energy where you have a realistic chance.
• Spot newly-posted niche roles ("4 people clicked apply") before they
  blow up.
• Confirm at a glance whether a high-salary role is already over-subscribed.

HOW IT WORKS (no magic, no servers)
• 100% local. No accounts, no signups, no third-party servers.
• Reads each job's applicant count from the same page LinkedIn already
  loaded for you, or by silently visiting the job's detail URL (the same
  URL you'd click yourself).
• Strict throttling (≈1 request per 0.8 seconds, max 100 per browsing
  session) and adaptive back-off if LinkedIn rate-limits.
• Your filter selections persist across pages; cached counts clear when
  you close the browser.

WHAT IT DOES NOT DO
• Doesn't apply to jobs for you.
• Doesn't send any data anywhere except LinkedIn itself.
• Doesn't track you.

PRIVACY
• No analytics. No telemetry. No remote endpoints.
• All preference and cache data lives in your browser's local storage.
• Open source: github.com/<YOUR_USER>/linkedin-applicant-filter

NOTE ON LINKEDIN'S TERMS
LinkedIn's User Agreement section 8.2 broadly restricts automated access.
The extension only requests pages your own browser would request, but
running automated tools against LinkedIn is your decision. Personal-use
recommended.
```

---

## 3. Category

**Productivity** (primary), Tools (secondary).

---

## 4. Single-purpose statement (required by the Store)

> The single purpose of this extension is to display the applicant count of
> jobs on LinkedIn search results pages and let the user filter the results
> by applicant-count range.

---

## 5. Permission justifications (Store form asks for each)

| Permission | Justification |
|---|---|
| `storage` | Persist the user's selected filter buckets and the in-session cache of applicant counts. No data leaves the browser. |
| Host: `https://www.linkedin.com/*` | The extension's entire purpose is to operate on LinkedIn job pages. It (a) injects the filter bar UI on `/jobs/search/*`, and (b) fetches `/jobs/view/{jobId}/` to extract applicant counts not exposed in the search list. |

We do **not** request: `tabs`, `webRequest`, `cookies`, `activeTab`,
`scripting` (`world: "MAIN"` is declared in manifest content_scripts, not via
the `scripting` permission), `identity`, `notifications`, `<all_urls>`.

---

## 6. Privacy policy

You **must** publish this on a public URL (GitHub Pages or similar) and link
it in the Store form. Suggested text:

```markdown
# LinkedIn Applicant Filter — Privacy Policy

Last updated: 2025-06-09

## What we collect

NOTHING that leaves your browser. The extension:

1. Reads job-card metadata from the LinkedIn pages you visit (DOM + the
   page's own in-memory Ember store).
2. Fetches `https://www.linkedin.com/jobs/view/{jobId}/` for jobs whose
   applicant count isn't already in the page — using your existing LinkedIn
   session. These requests are identical in form to a normal browser
   navigation.
3. Stores extracted applicant counts in `chrome.storage.session` (cleared on
   browser close).
4. Stores your filter-bucket selections and collapsed-state in
   `chrome.storage.local` (persists until you uninstall the extension or
   clear browser data).

## What we share

NOTHING. The extension makes no requests to any server other than
linkedin.com, and no data is transmitted to the extension author or any
third party.

## What we don't do

- No analytics, no telemetry, no error reporting service.
- No advertising identifiers.
- No remote feature flags.
- No background tracking when you're not on LinkedIn.

## Contact

Open an issue on the project's GitHub repository.
```

---

## 7. Screenshots (1280×800 PNG, 1-5 required)

Located in `docs/screenshots/`:

- `main.png` (1280×720, padded to 1280×800 in `01_main_store.png`) —
  filter bar with 100+ selected, badges visible on cards.
- `filter-bar.png` (closeup) — for the README, not the Store.
- `list-pane.png` (left pane only) — alternative store shot.

**Take 2-3 more before submitting** to show:
- Filter bar with multiple buckets selected
- Filter bar in collapsed state (after clicking the up-arrow icon)
- A search where low-applicant jobs (0-10 or 10-30 bucket badges) are
  visible — that's the killer-feature shot.

---

## 8. Promotional images (optional but recommended)

- Small promo tile: 440×280
- Marquee tile (featured): 1400×560

Suggested visual: filter bar with a "0-10" bucket selected, with 3-4 cards
each showing a green badge — sells the "find hidden gems" pitch in one
glance.

---

## 9. Additional store fields

| Field | Value |
|---|---|
| Pricing | Free |
| Regions | All |
| Languages | English (add Chinese if you localize the UI) |
| Mature content | No |
| Support email | Your email |
| Support site | GitHub repo URL |
| Homepage | GitHub repo URL or landing page |

---

## 10. What you still need to do before clicking Publish

- [ ] Open a Chrome Web Store developer account ($5 one-time fee,
  https://chrome.google.com/webstore/devconsole)
- [ ] Host the privacy policy publicly (GitHub Pages is free; point at
  `docs/privacy-policy.md`)
- [ ] Replace the placeholder green-triangle icons in `icon/` with proper
  ones (export from Figma / canva at 16/48/128 — a funnel + LinkedIn-blue
  number badge would be on-brand)
- [ ] Take 2-3 more screenshots per §7 above
- [ ] Set the manifest `version` to `1.0.0` (currently bumped patch each
  install for dev; reset before zip)
- [ ] Zip the extension files (exclude `tests/`, `probes/`, `docs/`,
  `.github/`, `LICENSE`, `README.md`, `.gitignore`)
- [ ] Submit. Review usually takes 1-3 business days. **First submission
  often gets a follow-up question about the host permission scope — be
  ready to point to the single-purpose statement.**

---

## 11. Risks to know before submitting

- **LinkedIn could file a takedown** under the Chrome Web Store's
  "respects intellectual property and trademarks" policy. Risk is low for a
  personal-use, no-ads, no-scraping extension that only acts on the user's
  own pages, but not zero.
- **Google review may ask** why we need host permission for all of
  `linkedin.com/*`. The honest answer: the extension operates on
  `/jobs/search/*` (where the bar lives) AND fetches `/jobs/view/*` (where
  the counts live), and Chrome's match-pattern syntax doesn't let you split
  those cleanly across content scripts vs background fetches without a
  broader permission. Be ready to explain.
- **You're responsible for LinkedIn account safety** of users who install
  it. The README has the honest framing — keep that wording in any user-
  facing copy.

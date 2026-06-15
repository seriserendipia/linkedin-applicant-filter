# Contributing

Thanks for your interest. This is a small, personal-use project — issues and
PRs are welcome but please keep the scope tight.

## Project shape

It's a plain Manifest V3 Chrome extension — **no build step, no bundler**. The
source files are loaded directly:

| File | Role |
|---|---|
| `manifest.json` | MV3 declaration |
| `parser.js` | Applicant-text regex + bucket logic (pure, dual-exported for Node tests) |
| `background.js` | Service worker: fetch queue, 3 workers, adaptive backoff |
| `content.js` | ISOLATED world: filter bar UI, badges, filtering, resilience |
| `main_world.js` | MAIN world: Ember-store sweep (classic board) + React-fiber jobId stamping (AI/SDUI layout) |
| `content.css` | Filter bar + badge styles |

See the [README architecture section](README.md#architecture) for how the
pieces fit, and `probes/` for the investigation scripts that shaped the design.

## Running locally

1. `chrome://extensions/` → enable **Developer mode** → **Load unpacked** →
   select this folder.
2. Open any `https://www.linkedin.com/jobs/search/*` page.

When you edit files, click the **refresh** icon on the extension card (Chrome
caches the service worker bytecode aggressively — a plain file save isn't
always enough).

## Tests

```bash
# Unit tests — no browser, runs in CI
node tests/test_parser.js

# E2E + perf — require a real Chrome with a logged-in LinkedIn profile and an
# Xvfb display; see test headers for the setup. Not run in CI (need LinkedIn login).
pytest tests/test_e2e.py -v
```

CI (`.github/workflows/ci.yml`) runs the JS syntax check, the parser unit
tests, and a manifest sanity check on every push/PR.

## Conventions

- Vanilla JS, no dependencies. Keep it that way unless there's a strong reason.
- Match the existing comment density — explain *why* when it's non-obvious
  (especially LinkedIn-DOM quirks), not *what*.
- If you touch the parser, add a case to `tests/test_parser.js`.
- LinkedIn changes its DOM often. Prefer resilient selectors and always keep
  the graceful-fallback path working (see `content.js` give-up logic).

## Scope / non-goals

- No analytics, telemetry, or remote endpoints. Ever.
- No collecting or transmitting personal data.
- No Firefox/Safari port (the code uses Chromium-only APIs).

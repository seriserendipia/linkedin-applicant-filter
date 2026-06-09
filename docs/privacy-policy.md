# LinkedIn Applicant Filter — Privacy Policy

Last updated: 2025-06-09

## What we collect

**Nothing that leaves your browser.** The extension:

1. Reads job-card metadata from the LinkedIn pages you visit (DOM + the
   page's own in-memory store).
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

**Nothing.** The extension makes no requests to any server other than
linkedin.com, and no data is transmitted to the extension author or any
third party.

## What we don't do

- No analytics, no telemetry, no error reporting service.
- No advertising identifiers.
- No remote feature flags.
- No background tracking when you're not on LinkedIn.

## Contact

Open an issue on the project's GitHub repository.

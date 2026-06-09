/**
 * Unit tests for parser.js — covers every applicant-text format we've observed
 * on LinkedIn (across all the probe runs) plus negative cases.
 *
 * Run: node tests/test_parser.js
 * Exit 0 = all pass, non-zero = at least one failure.
 */
const { parseApplicantCount, bucketFor, BUCKETS, UNKNOWN } = require("../parser");

let pass = 0, fail = 0;
const failures = [];

function eq(actual, expected, label) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) { pass++; return; }
  fail++;
  failures.push(`✗ ${label}\n   expected: ${JSON.stringify(expected)}\n   actual:   ${JSON.stringify(actual)}`);
}

// ── parseApplicantCount: positive cases (real LinkedIn formats from probes)
[
  // [input, expected: {count, kind, raw}]
  ["4 people clicked apply",                  { count: 4,   kind: "exact",  raw: "4 people clicked apply" }],
  ["23 people applied",                       { count: 23,  kind: "exact",  raw: "23 people applied" }],
  ["47 applicants",                           { count: 47,  kind: "exact",  raw: "47 applicants" }],
  ["1 applicant",                             { count: 1,   kind: "exact",  raw: "1 applicant" }],
  ["Over 100 applicants",                     { count: 100, kind: "over",   raw: "Over 100 applicants" }],
  ["over 100 people clicked apply",           { count: 100, kind: "over",   raw: "over 100 people clicked apply" }],
  ["Under 10 applicants",                     { count: 9,   kind: "under",  raw: "Under 10 applicants" }],
  ["less than 25 applicants",                 { count: 24,  kind: "under",  raw: "less than 25 applicants" }],
  ["Be among the first 25 applicants",        { count: 25,  kind: "firstN", raw: "Be among the first 25 applicants" }],
  ["Be among the first applicants",           { count: 10,  kind: "firstN", raw: "Be among the first applicants" }],
].forEach(([input, expected]) => eq(parseApplicantCount(input), expected, `parse: ${JSON.stringify(input)}`));

// ── parseApplicantCount: noise — substring matches inside real HTML
const html_with_count = `<span class="_5cbf41a4">Over 100 applicants</span><span>About the job</span>`;
eq(parseApplicantCount(html_with_count)?.kind, "over", "parse: HTML with span wrapper");

const html_with_count_2 = `\"children\":[\"4 people clicked apply\"]}],\"$undefined\"`;
eq(parseApplicantCount(html_with_count_2)?.count, 4, "parse: HTML/JSON escaped form");

// ── parseApplicantCount: negative cases
[
  ["",                                  null],
  [null,                                null],
  [undefined,                           null],
  [42,                                  null],
  ["random text without any count",     null],
  ["404 people viewed this page",       null],   // 'viewed', not 'applied/clicked apply'
  ["Posted 100 days ago",               null],
  ["100 employees on LinkedIn",         null],
  ["3 days ago · Be the first one",     null],   // wrong phrase
].forEach(([input, expected]) => eq(parseApplicantCount(input), expected, `parse: ${JSON.stringify(input)} (negative)`));

// ── bucketFor: each bucket boundary
eq(bucketFor({ count: 0,   kind: "exact" }),  "0-10",  "bucket 0 → 0-10");
eq(bucketFor({ count: 9,   kind: "exact" }),  "0-10",  "bucket 9 → 0-10");
eq(bucketFor({ count: 10,  kind: "exact" }),  "10-30", "bucket 10 → 10-30 (boundary)");
eq(bucketFor({ count: 29,  kind: "exact" }),  "10-30", "bucket 29 → 10-30");
eq(bucketFor({ count: 30,  kind: "exact" }),  "30-50", "bucket 30 → 30-50 (boundary)");
eq(bucketFor({ count: 49,  kind: "exact" }),  "30-50", "bucket 49 → 30-50");
eq(bucketFor({ count: 50,  kind: "exact" }),  "50-100","bucket 50 → 50-100 (boundary)");
eq(bucketFor({ count: 99,  kind: "exact" }),  "50-100","bucket 99 → 50-100");
eq(bucketFor({ count: 100, kind: "exact" }),  "100+",  "bucket 100 → 100+ (boundary)");
eq(bucketFor({ count: 500, kind: "exact" }),  "100+",  "bucket 500 → 100+");

// ── bucketFor: 'over' kind always maps to 100+
eq(bucketFor({ count: 100, kind: "over" }),   "100+",  "bucket over 100 → 100+");

// ── bucketFor: 'under' uses derived count
eq(bucketFor({ count: 9,   kind: "under" }),  "0-10",  "bucket under-10 → 0-10");
eq(bucketFor({ count: 24,  kind: "under" }),  "10-30", "bucket under-25 → 10-30");

// ── bucketFor: 'firstN' bucketed by N
eq(bucketFor({ count: 25,  kind: "firstN" }), "10-30", "bucket firstN=25 → 10-30");
eq(bucketFor({ count: 10,  kind: "firstN" }), "10-30", "bucket firstN=10 → 10-30");

// ── bucketFor: degenerate
eq(bucketFor(null),                            UNKNOWN, "bucket null → unknown");
eq(bucketFor(undefined),                       UNKNOWN, "bucket undefined → unknown");
eq(bucketFor({ count: -1, kind: "exact" }),    UNKNOWN, "bucket negative → unknown");
eq(bucketFor({ count: NaN, kind: "exact" }),   UNKNOWN, "bucket NaN → unknown");

// ── BUCKETS metadata sanity
eq(BUCKETS.length, 5, "five buckets defined");
eq(BUCKETS.map(b => b.id), ["0-10","10-30","30-50","50-100","100+"], "bucket order");

// ── report
console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) {
  console.log("\nFailures:");
  for (const f of failures) console.log("  " + f);
  process.exit(1);
}

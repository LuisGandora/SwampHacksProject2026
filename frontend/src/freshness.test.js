import test from "node:test";
import assert from "node:assert/strict";
import { scoreIsCurrent } from "./freshness.js";

test("score dates use site-local day around midnight, not the browser's UTC day", () => {
  const site = { timezone: "America/New_York", scored_for: "2026-10-17T16:00:00Z" };
  assert.equal(scoreIsCurrent(site, "now", new Date("2026-10-18T02:00:00Z")), true);
  assert.equal(scoreIsCurrent(site, "now", new Date("2026-10-18T06:00:00Z")), false);
});

test("tomorrow crosses month and daylight-saving boundaries by calendar day", () => {
  const site = { timezone: "America/New_York", scored_for: "2026-11-01T17:00:00Z" };
  assert.equal(scoreIsCurrent(site, "next_day", new Date("2026-10-31T22:00:00Z")), true);
  assert.equal(scoreIsCurrent(site, "now", new Date("2026-10-31T22:00:00Z")), false);
});

test("missing and invalid score timestamps are unverified", () => {
  assert.equal(scoreIsCurrent({}, "now"), false);
  assert.equal(scoreIsCurrent({ timezone: "America/Chicago", scored_for: "invalid" }, "now"), false);
});

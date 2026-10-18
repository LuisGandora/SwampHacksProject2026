import test from "node:test";
import assert from "node:assert/strict";
import { alertTiming, countdown, fmtCrewHours, fmtProbability } from "./hazard.js";

const now = Date.parse("2026-10-18T05:00:00Z");
test("invalid alert expiry never renders NaN", () => {
  assert.equal(countdown("invalid", now), null);
  assert.equal(alertTiming({ category: "flooding", all_clear_at: "invalid" }, now), null);
});

test("river and wildlife expiry do not promise all-clear", () => {
  for (const category of ["flooding", "wildlife", "high_wind"]) {
    const text = alertTiming({ category, all_clear_at: "2026-10-18T07:00:00Z" }, now);
    assert.doesNotMatch(text, /all.clear/i);
    assert.match(text, /expires/i);
  }
});

test("lightning countdown remains conditional on fresh conditions", () => {
  const text = alertTiming({ category: "lightning", all_clear_at: "2026-10-18T05:30:00Z" }, now);
  assert.match(text, /30:00/);
  assert.match(text, /verify/i);
});

test("small nonzero estimates do not appear to be zero", () => {
  assert.equal(fmtCrewHours(0.012), "<0.1");
  assert.equal(fmtCrewHours(0.08), "<0.1");
  assert.equal(fmtCrewHours(0), "0.0");
  assert.equal(fmtCrewHours(null), "–");
  assert.equal(fmtProbability(0.00004), "<0.1%");
  assert.equal(fmtProbability(0), "0.0%");
});

test("expiry names the site's timezone and date across midnight", () => {
  const text = alertTiming({ category: "flooding", timezone: "America/Chicago",
    all_clear_at: "2026-10-19T05:30:00Z" }, now);
  assert.match(text, /Oct 19/);
  assert.match(text, /12:30 AM/);
  assert.match(text, /CDT/);
});

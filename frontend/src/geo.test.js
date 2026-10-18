import test from "node:test";
import assert from "node:assert/strict";
import { milesBetween, nearbyAlerts } from "./geo.js";

const now = Date.parse("2026-10-18T05:00:00Z");
const point = { lat: 0, lon: 0 };
const latitudeAtMiles = (miles) => miles / 3958.8 * 180 / Math.PI;
const alert = (id, miles, extra = {}) => ({ alert_id: id, site_id: id, lat: latitudeAtMiles(miles), lon: 0,
  level: "warning", all_clear_at: "2026-10-18T06:00:00Z", ...extra });

test("nearby alerts include the selected site and neighbors through 10 miles, nearest first", () => {
  const input = [alert(3, 10.01), alert(2, 9.99), alert(1, 0), alert(4, 4)];
  const result = nearbyAlerts(input, point, now);
  assert.deepEqual(result.map((a) => a.alert_id), [1, 4, 2]);
  assert.ok(Math.abs(result[1].distance_mi - 4) < 1e-9);
  assert.equal(input[0].distance_mi, undefined);
  const edge = alert(5, 10);
  assert.equal(nearbyAlerts([edge], point, now, milesBetween(0, 0, edge.lat, edge.lon)).length, 1);
});

test("expired, cleared, invalid-expiry and unlocated alerts are excluded", () => {
  assert.deepEqual(nearbyAlerts([
    alert(1, 1, { all_clear_at: "2026-10-18T05:00:00Z" }),
    alert(2, 1, { cleared_at: "2026-10-18T04:00:00Z" }),
    alert(3, 1, { lat: null }), alert(4, 1, { lon: undefined }),
    alert(5, 1, { all_clear_at: "invalid" }), alert(6, 1, { lat: 91 }),
  ], point, now), []);
  assert.equal(nearbyAlerts([alert(7, 1, { all_clear_at: null })], point, now).length, 1);
  assert.deepEqual(nearbyAlerts([alert(1, 1)], null, now), []);
});

test("distance works across the antimeridian", () => {
  const result = nearbyAlerts([{ ...alert(1, 0), lon: -179.99 }], { lat: 0, lon: 179.99 }, now);
  assert.equal(result.length, 1);
  assert.ok(result[0].distance_mi < 2);
});

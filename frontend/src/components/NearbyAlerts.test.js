import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("nearby panel shows distances and distinguishes current, stale, and loading states", async () => {
  const server = await createServer({ server: { middlewareMode: true, ws: false }, appType: "custom" });
  try {
    const { default: NearbyAlerts } = await server.ssrLoadModule("/src/components/NearbyAlerts.jsx");
    const now = new Date("2026-10-18T05:00:00Z");
    const props = { location: { lat: 29, lon: -81 }, alerts: [], categories: [], now,
      reliable: true, lastUpdated: now.getTime(), onSelect: () => {} };
    const render = (extra = {}) => renderToStaticMarkup(React.createElement(NearbyAlerts, { ...props, ...extra }));
    assert.match(render(), /No active site alerts reported within 10 miles/);
    assert.match(render({ lastUpdated: null }), /Waiting for live alert data/);
    const stale = render({ lastUpdated: now.getTime() - 60000 });
    assert.match(stale, /live status is unverified/);
    assert.doesNotMatch(stale, /No active site alerts reported/);
    const shown = render({ alerts: [{ alert_id: 1, site_id: 7, site_name: "Neighbor Plant", lat: 29.01, lon: -81,
      category: "flooding", level: "stop_work", all_clear_at: "2026-10-18T06:00:00Z", message: "Rising river" }] });
    assert.match(shown, /1 active alert within 10 miles/);
    assert.match(shown, /STOP WORK/);
    assert.match(shown, /Neighbor Plant/);
    assert.match(shown, /0.7.*mi to alert site/);
    assert.match(shown, /not the hazard itself/);
  } finally {
    await server.close();
  }
});

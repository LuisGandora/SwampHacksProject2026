import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("an expired non-lightning alert stays stop-work until refreshed", async () => {
  const server = await createServer({ server: { middlewareMode: true, ws: false }, appType: "custom" });
  try {
    const { default: Banner } = await server.ssrLoadModule("/src/components/StopWorkBanner.jsx");
    const html = renderToStaticMarkup(React.createElement(Banner, {
      alerts: [{ alert_id: 1, site_id: 7, site_name: "Plant A", level: "stop_work", category: "flooding",
        all_clear_at: "2026-10-18T04:00:00Z", last_trigger_at: "2026-10-18T02:00:00Z" }],
      selectedId: 7, now: new Date("2026-10-18T05:00:00Z"), categories: [], reliable: true,
    }));
    assert.match(html, /STOP WORK/);
    assert.match(html, /Awaiting fresh data/);
    assert.doesNotMatch(html, /stopwork--clear|ALL-CLEAR IN|00:00|New strike/);
  } finally {
    await server.close();
  }
});

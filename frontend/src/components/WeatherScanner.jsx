import { useEffect, useState } from "react";
import { api } from "../api.js";
import ForecastChart from "./ForecastChart.jsx";

// Weather scanner for one site: right now, the past 7 days measured at its station, the next 7 days forecast,
// and the nearest lightning. Opening it pulls fresh NWS data if stale; the worker refreshes it at 6 PM local.
export default function WeatherScanner({ siteId, timezone, refreshKey }) {
  const [w, setW] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = (refresh = false) => {
    setBusy(true);
    return api.weather(siteId, refresh).then((d) => { setW(d); setError(""); })
      .catch((e) => setError(e.message)).finally(() => setBusy(false));
  };
  useEffect(() => {
    let live = true, pending = false;
    setW(null); setError("");
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await api.weather(siteId);
        if (live) { setW(data); setError(""); }
      } catch (e) { if (live) setError(`Weather freshness could not be verified. ${e.message}`); }
      finally { pending = false; }
    };
    poll();
    const interval = setInterval(poll, 60000);
    return () => { live = false; clearInterval(interval); };
  }, [siteId, refreshKey]);

  if (error) return <p className="error small">{error}</p>;
  if (!w) return <p className="muted small">Scanning weather: pulling the past week and the 7-day forecast…</p>;

  const time = (t) => t && new Date(t).toLocaleString("en-US", { timeZone: timezone, weekday: "short", hour: "numeric", minute: "2-digit" });
  const now = w.latest_reading;
  const bolt = w.lightning;
  const boltClass = bolt.nearest_mi == null ? "muted" : bolt.nearest_mi <= 10 ? "stop" : bolt.nearest_mi <= 20 ? "warn" : "";
  const minutesAgo = bolt.nearest_at && Math.max(0, Math.round((Date.now() - new Date(bolt.nearest_at)) / 60000));
  const rows = [...w.past_hours, ...w.next_hours];
  const today = new Date().toLocaleDateString("en-CA", { timeZone: timezone });

  return (
    <section className="scanner">
      <div className="forecast-head">
        <h4>Weather scanner</h4>
        <button className="link small" onClick={() => load(true)} disabled={busy}>{busy ? "Scanning…" : "↻ Refresh"}</button>
      </div>
      <p className="small muted">
        Station K{w.station} · source {now?.source || "unavailable"} · reading {time(now?.observed_at) || "none"} · forecast issued {time(w.forecast_issued_at) || "none"} ·
        refreshes when opened and at 6 PM local
      </p>
      {now && (
        <div className="now-strip">
          <span><strong>{now.temp_f == null ? "Temperature unavailable" : `${Math.round(now.temp_f)}°F`}</strong>{now.heat_index_f && now.heat_index_f > now.temp_f ? ` · feels ${Math.round(now.heat_index_f)}°F` : ""}</span>
          <span>Wind {now.wind_mph == null ? "unavailable" : `${Math.round(now.wind_mph)} mph`}{now.wind_gust_mph ? `, gusts ${Math.round(now.wind_gust_mph)}` : ""}</span>
          <span>Rain last hour {now.precip_1h_in == null ? "unavailable" : `${Number(now.precip_1h_in).toFixed(2)} in`}</span>
        </div>
      )}
      <p className={`small ${boltClass}`}>
        {bolt.nearest_mi == null ? "⚡ No lightning within 100 mi in the last 30 min" :
          `⚡ Nearest lightning ${bolt.nearest_mi} mi ${bolt.direction}, ${minutesAgo} min ago · ` +
          `${bolt.within_10_mi} strikes within 10 mi, ${bolt.within_20_mi} within 20 mi`}
      </p>
      <ForecastChart rows={rows} timezone={timezone} title="Past 7 days (measured) · next 7 days (NWS forecast)"
                     nowIndex={w.past_hours.length} />
      <div className="table-wrap">
        <table className="data-table daily">
          <thead><tr><th>Day</th><th>Hi/lo</th><th>Rain</th><th>Gust</th><th>Thund.</th></tr></thead>
          <tbody>{w.daily.map((d) => (
            <tr key={d.day} className={d.day === today ? "on" : d.source === "forecast" ? "muted" : ""}>
              <td>{new Date(`${d.day}T12:00`).toLocaleDateString("en-US", { weekday: "short", month: "numeric", day: "numeric" })}
                {d.day === today ? " today" : d.source === "forecast" ? " fc" : ""}</td>
              <td>{d.high_f ?? "–"} / {d.low_f ?? "–"}</td>
              <td>{d.rain_in ?? "–"}</td>
              <td>{d.max_gust_mph ?? "–"}</td>
              <td>{d.max_thunder_pct != null ? `${d.max_thunder_pct}%` : "–"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <p className="small muted">°F · rain in inches · gusts in mph · thunder = highest hourly chance · fc = forecast</p>
    </section>
  );
}

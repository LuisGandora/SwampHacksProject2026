import { useEffect, useState } from "react";
import { api } from "../api.js";
import { band, categoryLabel, countdown, fmtScore, usd } from "../hazard.js";
import ForecastChart from "./ForecastChart.jsx";

const COORD = {
  ok: ["✓ Location verified (OSM)", "ok"], corrected: ["✓ Location corrected to OSM outline", "ok"],
  review: ["Location: EIA and OSM differ 1–5 km", "warn"], unresolved: ["⚠ Location unresolved", "warn"],
  no_osm: ["Location: EIA only (no OSM match)", "muted"],
};

export default function SiteCard({ summary, horizon, categories, alerts, now, dev, onReport, onAiReport,
                                   onScenario, onStrike, onClearScenario, refreshKey, onClose }) {
  const [detail, setDetail] = useState(null);
  const [forecast, setForecast] = useState([]);
  const [crew, setCrew] = useState([]);
  const [briefing, setBriefing] = useState(undefined);
  const [econ, setEcon] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const id = summary.site_id;

  useEffect(() => {
    let live = true;
    setError(""); setBriefing(undefined); setEcon(null); setLoading(true);
    Promise.all([api.site(id), api.forecast(id), api.crewReports(id)])
      .then(([d, f, c]) => live && (setDetail(d), setForecast(f), setCrew(c)))
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [id, refreshKey]);

  // Collapsible sections load their data the first time they're opened.
  const loadBriefing = (e) => e.target.open && briefing === undefined &&
    api.briefing(id).then(setBriefing).catch((err) => setBriefing(err.status === 404 ? null : { error: err.message }));
  const loadEconomics = (e) => e.target.open && !econ &&
    api.economics(id).then(setEcon).catch((err) => setEcon({ error: err.message }));

  const cs = summary.category_scores || {};
  const meta = cs._meta || {};
  const cats = Object.entries(cs).filter(([k, v]) => k !== "_meta" && v.score >= 0.1).sort((a, b) => b[1].score - a[1].score);
  const mine = alerts.filter((a) => a.site_id === id).sort((a, b) => (a.level === "stop_work" ? -1 : 1));
  const tz = summary.timezone;
  const [coordText, coordCls] = COORD[summary.coord_check] || ["", "muted"];
  const p = detail?.risk_profile;

  return (
    <section className="panel site-card" aria-label={`Details for ${summary.name}`}>
      <header className="panel-head">
        <div>
          <p className="eyebrow">SITE {id} · {summary.primary_technology}</p>
          <h2>{summary.name}</h2>
          <p className="muted small">
            {summary.operator} · {summary.county}, {summary.state} ·{" "}
            {now.toLocaleTimeString("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit", timeZoneName: "short" })}
          </p>
          <p className={`small ${coordCls}`}>{coordText}</p>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close site">✕</button>
      </header>

      <div className="score-tiles">
        {[["Today", summary.score_now], ["Tomorrow", summary.score_next_day]].map(([label, s]) => {
          const b = band(s);
          return (
            <div key={label} className="score-tile" style={{ borderColor: b.color }}>
              <span className="muted small">{label}</span>
              <strong className="big">{fmtScore(s)}<small>/10</small></strong>
              <span className="band-label"><i className="dot" style={{ background: b.color }} />{b.label}</span>
            </div>
          );
        })}
        <div className="score-tile">
          <span className="muted small">Expected crew impact</span>
          <strong className="big">{meta.expected_crew_hours_lost != null ? meta.expected_crew_hours_lost.toFixed(1) : "–"}<small> crew-h</small></strong>
          <span className="small muted" title="Probability × hours lost per hazard × crew size × cost: all labeled assumptions">
            ≈ {usd(meta.expected_cost_usd)} · estimate*
          </span>
        </div>
      </div>

      <div className="actions">
        <button className="primary" onClick={onAiReport}>🛰 AI safety report</button>
        <button onClick={onReport}>📍 Report hazard here</button>
      </div>

      {mine.length > 0 && (
        <section>
          <h4>Active alerts ({mine.length})</h4>
          <ul className="alert-list">
            {mine.map((a) => (
              <li key={a.alert_id} className={`alert alert--${a.level}`}>
                <span className="alert-icon" aria-hidden="true">{a.level === "stop_work" ? "⛔" : "⚠"}</span>
                <div>
                  <strong>{a.title}</strong>
                  <p className="small">{a.message}</p>
                  {countdown(a.all_clear_at, now.getTime()) && (
                    <p className="small mono">All-clear in {countdown(a.all_clear_at, now.getTime())}</p>
                  )}
                </div>
                {a.image_url && <img src={a.image_url} alt="" className="thumb" loading="lazy" />}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4>{horizon === "now" ? "Today" : "Tomorrow"}: what drives the score</h4>
        {cats.length === 0 ? <p className="muted small">No hazard above 0.1/10.</p> : (
          <ul className="cat-bars">
            {cats.slice(0, 6).map(([k, v]) => {
              const b = band(v.score);
              return (
                <li key={k}>
                  <span className="cat-name">{categoryLabel(k, categories)}</span>
                  <span className="bar"><span style={{ width: `${v.score * 10}%`, background: b.color }} /></span>
                  <span className="cat-score mono">{fmtScore(v.score)}</span>
                  {v.drivers?.length > 0 && (
                    <span className="cat-drivers small muted">
                      {v.drivers.map((d) => `${d.label} ${d.value}`).join(" · ")}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {loading ? <p className="muted small">Loading forecast…</p> : <ForecastChart rows={forecast} timezone={tz} />}

      <details key={`b${id}`} onToggle={loadBriefing}>
        <summary>6 PM next-day briefing</summary>
        {briefing === undefined ? <p className="muted small">Loading…</p>
          : briefing === null ? <p className="muted small">No briefing yet: it's written at 6 PM local time.</p>
          : briefing.error ? <p className="error small">{briefing.error}</p>
          : (
            <div>
              <p className="small muted">For {briefing.briefing_date} · sent {new Date(briefing.sent_at).toLocaleString()}</p>
              <p>{briefing.summary}</p>
            </div>
          )}
      </details>

      {p && (
        <details>
          <summary>Site history (2015–2026)</summary>
          <dl className="stats">
            <div><dt>Severe storms ≤25 mi</dt><dd>{p.severe_storm_events ?? 0}</dd></div>
            <div><dt>Tropical ≤50 mi</dt><dd>{p.tropical_events ?? 0}</dd></div>
            <div><dt>Floods ≤10 mi</dt><dd>{p.flood_events ?? 0}</dd></div>
            <div><dt>Lightning ≤10 mi</dt><dd>{p.lightning_events ?? 0}</dd></div>
            <div><dt>Heat-warning days/yr</dt><dd>{p.heat_warn_days_per_year ?? "–"}</dd></div>
            <div><dt>Outdoor storm deaths</dt><dd>{p.storm_fatalities_crew_relevant ?? 0}</dd></div>
            <div><dt>OSHA PPE reports ≤50 mi</dt><dd>{p.ppe_osha_reports ?? 0}</dd></div>
            <div><dt>County weather deaths</dt><dd>{p.cdc_county_suppressed && p.cdc_county_weather_deaths == null ? "<10" : p.cdc_county_weather_deaths ?? "–"}</dd></div>
          </dl>
          <p className="small muted">NOAA Storm Events, OSHA, CDC WONDER, within each hazard's radius.</p>
        </details>
      )}

      <details key={`e${id}`} onToggle={loadEconomics}>
        <summary>Economic impact</summary>
        {!econ ? <p className="muted small">Loading…</p> : econ.error ? <p className="error small">{econ.error}</p> : (
          <div>
            <p className="small">{econ.utility_name} · grid operator {econ.balancing_authority || "–"}</p>
            {econ.weekly.length > 0 && (
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr><th>Week</th><th>Demand MWh</th><th>Stop-work site-days</th><th>Crew-h lost*</th><th>Cost*</th></tr></thead>
                  <tbody>{econ.weekly.map((w) => (
                    <tr key={w.week}><td>{w.week}</td><td>{w.demand_mwh ? Math.round(w.demand_mwh).toLocaleString() : "–"}</td>
                      <td>{w.stop_work_site_days}</td><td>{Math.round(w.crew_hours_lost)}</td><td>{usd(w.est_cost_usd)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {econ.correlations.slice(0, 3).map((c) => (
              <p key={`${c.entity}${c.metric_x}${c.metric_y}`} className="small">
                {c.metric_x.replace(/_/g, " ")} vs {c.metric_y.replace(/_/g, " ")} ({c.entity}): r = {c.r}
                {c.p_value < 0.001 ? ", p < 0.001" : `, p = ${Number(c.p_value).toFixed(3)}`} · n = {c.n}, deseasonalized
              </p>
            ))}
            <p className="small muted">*Estimates from labeled assumptions (crew size, cost/hour, hours lost per hazard). Correlation is not causation.</p>
          </div>
        )}
      </details>

      <section>
        <h4>Crew reports ({crew.length})</h4>
        {crew.length === 0 ? <p className="muted small">No open reports at this site.</p> : (
          <ul className="crew-list">
            {crew.map((c) => (
              <li key={c.report_id}>
                {c.photo_url && <a href={c.photo_url} target="_blank" rel="noreferrer"><img src={c.photo_url} alt="" className="thumb" loading="lazy" /></a>}
                <div>
                  <strong>{categoryLabel(c.category, categories)}</strong>
                  {c.severity && <span className="muted small"> · severity {c.severity}/5</span>}
                  <p className="small">{c.description || "No description"}</p>
                  <p className="small muted">{c.reported_by || "Crew"} · {new Date(c.reported_at).toLocaleString()}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {dev && (
        <section className="dev">
          <h4>Demo controls</h4>
          <div className="actions">
            <button onClick={onScenario}>▶ Run brief scenario</button>
            <button onClick={onStrike}>⚡ Strike 3 mi away</button>
            <button className="ghost" onClick={onClearScenario}>Clear demo</button>
          </div>
        </section>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

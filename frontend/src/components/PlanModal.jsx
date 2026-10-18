import { useEffect, useState } from "react";
import { api } from "../api.js";
import { band, categoryLabel, fmtScore, usd } from "../hazard.js";

const TABS = [["now", "Act now"], ["week", "This week"], ["invest", "Invest"], ["economics", "Economics"]];
const signed = (v) => `${v < 0 ? "−" : "+"}${usd(Math.abs(v))}`;
const day = (d) => new Date(`${d}T12:00`).toLocaleDateString("en-US", { weekday: "short", month: "numeric", day: "numeric" });
const ICON = { heat: "☀️", storm: "⚡", rain: "🌧", wind: "💨" };

// Supervisor plan for the sites in view: what needs action now, what to schedule or stage this week, and where an
// investment pays back most. Costs of fixes are labeled assumptions (edit economic_assumptions).
export default function PlanModal({ sites, categories, onSelect, onClose, identity }) {
  const [tab, setTab] = useState("now");
  const [data, setData] = useState({});
  const [error, setError] = useState("");
  const ids = sites.map((s) => s.site_id);   // more than 1,000 sites in view: ask for all (the URL would be too long)

  useEffect(() => {
    if (data[tab]) return;
    setError("");
    api.plan(tab, ids.length <= 1000 ? ids : null).then((d) => setData((x) => ({ ...x, [tab]: d }))).catch((e) => setError(e.message));
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const go = (id) => { onSelect(id); onClose(); };
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState("");
  const saveAssumption = async (key) => {
    setSaving(key); setError("");
    try {
      await api.setAssumption(key, Number(edits[key]));
      setEdits((e) => { const { [key]: _, ...rest } = e; return rest; });
      const fresh = await api.plan("economics", ids.length <= 1000 ? ids : null);
      setData({ economics: fresh });   // week, invest and economics all use the assumptions: drop the others
    } catch (e) { setError(e.message); }
    setSaving("");
  };
  const d = data[tab];
  const weekDays = tab === "week" && d ? [...new Set(d.sites.flatMap((s) => s.days.map((x) => x.day)))].sort() : [];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal--wide" role="dialog" aria-modal="true" aria-label="Supervisor plan" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">SUPERVISOR PLAN · {sites.length} SITES IN VIEW</p>
            <h3>Where to focus attention and resources first</h3>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="seg plan-tabs" role="tablist">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        {!d && !error && <p className="muted">Working it out…</p>}

        {tab === "now" && d && (d.length === 0 ? <p className="muted">No site in view needs action right now.</p> : (
          <ol className="plan-list">
            {d.map((s) => (
              <li key={s.site_id}>
                <div className="plan-row">
                  <button className="link" onClick={() => go(s.site_id)}><strong>{s.name}</strong></button>
                  <span className="small muted">{s.county}, {s.state}</span>
                  <span className="band-label"><i className="dot" style={{ background: band(Number(s.score)).color }} />{fmtScore(s.score)}</span>
                  {s.stop_work > 0 && <span className="badge badge--stop">STOP WORK</span>}
                </div>
                <p className="small">{s.alerts.length ? s.alerts.map((a) => `${a.title}: ${a.message || ""}`).join(" · ") : s.reason || "High forecast hazard"}</p>
                {s.actions.length > 0 && <p className="small"><strong>Do:</strong> {s.actions.join(" ")}</p>}
                {s.resources.length > 0 && <p className="small"><strong>Bring:</strong> {s.resources.join("; ")}</p>}
              </li>
            ))}
          </ol>
        ))}

        {tab === "week" && d && (
          <div>
            {d.suggestions.length === 0 ? <p className="muted">No heat, storm, heavy-rain or wind days in the 7-day forecast for these sites.</p> : (
              <ul className="plan-list">{d.suggestions.map((s) => <li key={s.hazard}><p>{s.text}</p></li>)}</ul>
            )}
            <div className="table-wrap plan-grid">
              <table className="data-table">
                <thead><tr><th>Site</th>{weekDays.map((x) => <th key={x}>{day(x)}</th>)}<th>At risk*</th></tr></thead>
                <tbody>{[...d.sites].sort((a, b) => Object.values(b.counts).reduce((x, y) => x + y, 0) - Object.values(a.counts).reduce((x, y) => x + y, 0))
                  .filter((s) => Object.values(s.counts).some(Boolean)).slice(0, 40).map((s) => (
                  <tr key={s.site_id}>
                    <td><button className="link small" onClick={() => go(s.site_id)}>{s.name}</button>{s.fema_sfha ? " 🌊" : ""}{s.behind_levee ? " 🧱" : ""}</td>
                    {weekDays.map((day) => {
                      const x = s.days.find((y) => y.day === day);
                      return <td key={day} title={x ? `heat ${x.heat_f}°F · rain ${x.rain_in} in · thunder ${x.thunder ?? 0}%` : "no forecast yet"}>
                        {x ? Object.keys(ICON).filter((k) => x[k]).map((k) => ICON[k]).join("") || "·" : ""}</td>;
                    })}
                    <td className="small">{s.crew_hours} h · {usd(s.usd)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <p className="small"><strong>At risk this week (estimate*):</strong> {d.total.crew_hours.toLocaleString()} crew-hours ≈{" "}
              {usd(d.total.usd)} if crews of {d.total.crew_size} work every flagged site-day ·{" "}
              {d.by_day.filter((x) => x.usd).map((x) => `${day(x.day)} ${usd(x.usd)}`).join(" · ")}</p>
            <p className="small muted">☀️ heat index over the warning level · ⚡ 50%+ thunder · 🌧 1+ in of rain · 💨 sustained wind over the
              limit · 🌊 FEMA floodplain · 🧱 behind a levee. From the NWS 7-day forecast; sites with no flagged days are hidden.
              *Hours lost per hazard (capped at a shift) × crew size × the state's labor rate: assumptions you can change
              under Economics.</p>
          </div>
        )}

        {tab === "invest" && d && (
          <div>
            <div className="table-wrap plan-grid">
              <table className="data-table">
                <thead><tr><th>Site</th><th>Weather loss / yr*</th><th>Costliest hazard</th><th>Suggested fix</th><th>Fix cost*</th><th>Payback*</th><th>Also</th></tr></thead>
                <tbody>{d.sites.map((s) => (
                  <tr key={s.site_id}>
                    <td><button className="link small" onClick={() => go(s.site_id)}>{s.name}</button><span className="muted small"> {s.state}</span></td>
                    <td>{usd(s.annual_loss_usd)}</td>
                    <td className="wrap">{categoryLabel(s.top_hazard, categories)} ({s.stop_days_per_year?.[s.top_hazard]} days)</td>
                    <td className="wrap">{s.fix || "–"}</td>
                    <td>{s.fix_cost_usd ? usd(s.fix_cost_usd) : "–"}</td>
                    <td>{s.payback_years ? `${s.payback_years} yr` : "–"}</td>
                    <td className="wrap small">{[s.fema_sfha && `floodplain ${s.fema_flood_zone}`,
                      s.leveed_area_system && `behind levee (${(s.levee_accreditation || "").replace(" Levee System", "")})`,
                      s.crew_reports_1y > 0 && `${s.crew_reports_1y} crew reports`].filter(Boolean).join(" · ") || "–"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <p className="small muted">*Loss = stop-work weather days at the site per year (5 years of station records) × hours lost per
              hazard × crew size × the state's BLS labor rate. Fix costs and the share of lost time they avoid are labeled
              assumptions (economic_assumptions rows <code>mitigation_cost_*</code>, <code>mitigation_reduction_*</code>): replace them with
              real project costs.</p>
          </div>
        )}

        {tab === "economics" && d && (
          <div className="econ">
            <div className="econ-tiles">
              <div className="tile"><span className="tile-num">{usd(d.week.total.usd)}</span>
                <span className="tile-label">at risk this week*</span><span className="small muted">{d.week.total.crew_hours.toLocaleString()} crew-hours</span></div>
              <div className="tile"><span className="tile-num">{usd(d.annual_loss_usd)}</span>
                <span className="tile-label">weather loss / year*</span><span className="small muted">stop-work days, 5 yrs of records</span></div>
              <div className="tile"><span className="tile-num">{d.crew_dispatch.reduce((n, u) => n + u.outages, 0)}</span>
                <span className="tile-label">real outages near sites</span><span className="small muted">{d.crew_dispatch.reduce((n, u) => n + u.crews, 0)} with a crew assigned</span></div>
              <div className="tile"><span className="tile-num">{d.crew_trips.n}</span>
                <span className="tile-label">crew trips logged</span><span className="small muted">{d.crew_trips.company} company · {d.crew_trips.outage_map} outage map</span></div>
            </div>

            <h4>What the data shows</h4>
            <div className="econ-findings">
              {d.findings.map((f) => (
                <article key={f.title} className="panel">
                  <p><strong>{f.title}</strong> <span className={`badge ${f.verdict === "supported" ? "badge--live" : "badge--warn"}`}>{f.verdict.toUpperCase()}</span></p>
                  <p className="small">{f.text}</p>
                </article>
              ))}
            </div>

            <h4>By utility</h4>
            <div className="table-wrap plan-grid">
              <table className="data-table">
                <thead><tr><th>Utility</th><th>Sites</th><th>Weather loss / yr*</th><th>At risk this week*</th></tr></thead>
                <tbody>{d.utilities.filter((u) => u.annual_loss_usd || u.week_usd).slice(0, 15).map((u) => (
                  <tr key={u.utility}><td className="wrap">{u.utility}</td><td>{u.sites}</td><td>{usd(u.annual_loss_usd)}</td>
                    <td>{usd(u.week_usd)} · {u.week_crew_hours} h</td></tr>))}</tbody>
              </table>
            </div>

            <h4>Weather in real sales</h4>
            <p className="small muted">Each utility's reported monthly sales (EIA-861M) against degree days at its sites, the way utilities
              report a weather line to the SEC. Fit = share of month-to-month swing explained, without → with weather.</p>
            <div className="table-wrap plan-grid">
              <table className="data-table">
                <thead><tr><th>Utility</th><th>Fit</th><th>MWh per hot degree-day</th><th>Weather in sales, last 12 months</th></tr></thead>
                <tbody>{d.weather_in_sales.map((s) => (
                  <tr key={s.utility_name + s.state}><td className="wrap">{s.utility_name} ({s.state})</td>
                    <td>{Number(s.r2_without_weather).toFixed(2)} → {Number(s.r2).toFixed(2)}</td>
                    <td>{Math.round(s.mwh_per_cdd).toLocaleString()}</td><td>{signed(s.last_12_usd)}</td></tr>))}</tbody>
              </table>
            </div>

            <h4>Real crew dispatches (utility outage maps)</h4>
            {d.crew_dispatch.length === 0 ? <p className="small muted">None logged yet: the worker's crew_dispatch job polls the maps every 15 minutes.</p> : (
              <div className="table-wrap plan-grid">
                <table className="data-table">
                  <thead><tr><th>Utility</th><th>Outages near sites</th><th>Crew assigned</th><th>Open now</th><th>Hours to crew on site</th><th>Repair hours</th><th>Top cause</th><th>Since</th></tr></thead>
                  <tbody>{d.crew_dispatch.map((u) => (
                    <tr key={u.utility}><td>{u.utility}</td><td>{u.outages}</td><td>{u.crews}</td><td>{u.open_now}</td>
                      <td>{u.hours_to_on_site ?? "–"}</td><td>{u.repair_hours ?? "–"}</td><td className="wrap">{u.top_cause || "–"}</td>
                      <td>{new Date(u.since).toLocaleDateString()}</td></tr>))}</tbody>
                </table>
              </div>
            )}

            <details className="econ-assumptions">
              <summary>Assumptions behind the dollar figures ({d.assumptions.filter((a) => (a.note || "").startsWith("ASSUMPTION")).length} still assumed)</summary>
              <p className="small muted">{identity === "supervisor" ? "Type your company's number and Save: every figure above, Invest and the briefings update." :
                "Switch to your Supervisor identity to replace these with your company's numbers."} "Measured" is the average stop-work time per
                site-day from this app's own alerts.</p>
              <div className="table-wrap plan-grid">
                <table className="data-table">
                  <thead><tr><th>Setting</th><th>Value</th><th>Unit</th><th>Measured</th><th>Source</th></tr></thead>
                  <tbody>{d.assumptions.map((a) => (
                    <tr key={a.key}>
                      <td className="small">{a.key.replace(/_/g, " ")}</td>
                      <td>{identity === "supervisor" ? (
                        <span className="assume-edit">
                          <input type="number" min="0" step="any" value={edits[a.key] ?? Number(a.value)}
                                 onChange={(e) => setEdits((x) => ({ ...x, [a.key]: e.target.value }))} aria-label={a.key} />
                          {edits[a.key] !== undefined && Number(edits[a.key]) !== Number(a.value) &&
                            <button className="small" disabled={saving === a.key} onClick={() => saveAssumption(a.key)}>Save</button>}
                        </span>) : Number(a.value).toLocaleString()}</td>
                      <td className="small muted">{a.unit}</td>
                      <td className="small">{a.measured ? `${a.measured.avg_hours} h (${a.measured.site_days} site-days)` : ""}</td>
                      <td className="wrap small muted">{a.note}</td>
                    </tr>))}</tbody>
                </table>
              </div>
            </details>
            <p className="small muted">*Estimates: stop-work or flagged forecast days × hours lost per hazard × crew size × the state's
              labor rate (BLS until replaced). Sales and outages are real public records; correlation is not causation.</p>
          </div>
        )}
      </div>
    </div>
  );
}

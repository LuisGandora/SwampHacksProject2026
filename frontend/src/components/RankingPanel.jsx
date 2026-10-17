import { useMemo, useState } from "react";
import { BANDS, NO_SCORE, band, categoryLabel, fmtScore } from "../hazard.js";

const STATES = ["All", "FL", "GA", "AL"];

export default function RankingPanel({ sites, horizon, setHorizon, selectedId, onSelect, categories, loading }) {
  const [state, setState] = useState("All");
  const [query, setQuery] = useState("");
  const [onlyBand, setOnlyBand] = useState(null);

  const counts = useMemo(() => {
    const c = Object.fromEntries([...BANDS, NO_SCORE].map((b) => [b.key, 0]));
    sites.forEach((s) => (c[band(s.score).key] += 1));
    return c;
  }, [sites]);
  const stopSites = sites.filter((s) => s.stop_work_alerts > 0).length;

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sites.filter((s) => (state === "All" || s.state === state)
      && (!onlyBand || band(s.score).key === onlyBand)
      && (!q || s.name.toLowerCase().includes(q) || (s.operator || "").toLowerCase().includes(q)
          || (s.county || "").toLowerCase().includes(q)));
  }, [sites, state, query, onlyBand]);

  return (
    <section className="panel ranking" aria-label="Sites ranked by hazard index">
      <header className="panel-head">
        <h2>Hazard ranking</h2>
        <div className="seg" role="group" aria-label="Score horizon">
          {[["now", "Today"], ["next_day", "Tomorrow"]].map(([k, label]) => (
            <button key={k} className={horizon === k ? "on" : ""} onClick={() => setHorizon(k)}>{label}</button>
          ))}
        </div>
      </header>

      <div className="tiles" role="group" aria-label="Filter by band">
        {BANDS.map((b) => (
          <button key={b.key} className={`tile ${onlyBand === b.key ? "on" : ""}`}
                  onClick={() => setOnlyBand(onlyBand === b.key ? null : b.key)}
                  aria-pressed={onlyBand === b.key}>
            <span className="tile-num">{counts[b.key]}</span>
            <span className="tile-label"><i className="dot" style={{ background: b.color }} />{b.label}</span>
          </button>
        ))}
        <div className="tile tile--stop" title="Sites with an active stop-work alert">
          <span className="tile-num">{stopSites}</span>
          <span className="tile-label">⛔ Stop work</span>
        </div>
      </div>

      <div className="filters">
        <input type="search" placeholder="Search site, operator, county" value={query}
               onChange={(e) => setQuery(e.target.value)} aria-label="Search sites" />
        <div className="seg" role="group" aria-label="State">
          {STATES.map((s) => (
            <button key={s} className={state === s ? "on" : ""} onClick={() => setState(s)}>{s}</button>
          ))}
        </div>
      </div>

      <p className="muted small">
        {loading ? "Loading sites…" : `${shown.length} of ${sites.length} sites · Hazard Index = 10 × chance of at least one hazard ${horizon === "now" ? "today" : "tomorrow"}`}
      </p>

      <ol className="rank-list">
        {shown.map((s, i) => {
          const b = band(s.score);
          return (
            <li key={s.site_id}>
              <button className={`rank-row ${s.site_id === selectedId ? "on" : ""}`} onClick={() => onSelect(s.site_id)}>
                <span className="rank-num">{i + 1}</span>
                <span className="rank-main">
                  <span className="rank-name">{s.name}</span>
                  <span className="rank-sub">
                    {s.state} · {s.primary_technology}
                    {s.top_category && s.score >= 0.5 ? ` · ${categoryLabel(s.top_category, categories)}` : ""}
                  </span>
                  <span className="bar" aria-hidden="true">
                    <span style={{ width: `${(s.score ?? 0) * 10}%`, background: b.color }} />
                  </span>
                </span>
                <span className="rank-score">
                  <strong>{fmtScore(s.score)}</strong>
                  <span className="band-label"><i className="dot" style={{ background: b.color }} />{b.label}</span>
                  {s.stop_work_alerts > 0 && <span className="badge badge--stop">STOP ×{s.stop_work_alerts}</span>}
                  {s.warning_alerts > 0 && <span className="badge badge--warn">WARN ×{s.warning_alerts}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

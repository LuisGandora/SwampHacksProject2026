import { useEffect, useState } from "react";
import { api } from "../api.js";

const METRICS = [
  ["auc_any_hazard", "Any hazard (AUC)"],
  ["auc_severe_outcome", "Severe outcome (AUC)"],
  ["auc_death", "Death days (AUC)"],
  ["severe_days_in_top_10pct", "Severe days in top 10%"],
  ["calibration_any", "Calibration (1 = exact)"],
  ["spearman_damage_on_event_days", "Tracks damage (ρ)"],
];

// Backtest results, so supervisors and judges can see how much to trust the index.
export default function ModelModal({ onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { api.modelBacktest().then(setData).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const bt = data?.backtest;
  const m = data?.metrics;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal--wide" role="dialog" aria-modal="true" aria-label="Model accuracy" tabIndex={-1}
           onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">MODEL ACCURACY · {m?.version || "…"}</p>
            <h3>How well the Hazard Index has predicted real outcomes</h3>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>
        {error && <p className="error">{error}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && !bt && <p className="muted">No backtest yet: run <code>python -m ml.backtest</code>.</p>}
        {bt && (
          <div className="modal-body">
            <p className="small">
              Walk-forward backtest: for each test year the model is trained only on earlier years, then scored as if
              live and compared with what happened (NOAA events, deaths, injuries, damage, OSHA weather injuries). AUC:
              0.5 is a coin flip, 1.0 is perfect. Averages over {bt.folds.length} test years.
            </p>
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>Version</th>{METRICS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
                <tbody>
                  {Object.entries(bt.mean_over_folds).map(([v, row]) => (
                    <tr key={v} className={v.includes("production") ? "on" : ""}>
                      <td>{v}</td>{METRICS.map(([k]) => <td key={k}>{row[k]}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details>
              <summary>By test year</summary>
              {bt.folds.map((f) => (
                <div key={f.fold} className="table-wrap">
                  <p className="small"><strong>{f.fold}</strong></p>
                  <table className="data-table">
                    <thead><tr><th>Version</th>{METRICS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
                    <tbody>{Object.entries(f.variants).map(([v, row]) => (
                      <tr key={v}><td>{v}</td>{METRICS.map(([k]) => <td key={k}>{row[k] ?? "–"}</td>)}</tr>
                    ))}</tbody>
                  </table>
                </div>
              ))}
            </details>
            <p className="small muted">{bt.note} Severe outcome = a death, injury, $1M+ damage, or an OSHA weather injury
              to a worker. The index counts a severe outcome {m?.severity_weight}× an ordinary hazard event (an assumption).</p>
          </div>
        )}
      </div>
    </div>
  );
}

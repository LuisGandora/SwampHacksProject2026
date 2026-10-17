import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { categoryLabel } from "../hazard.js";

const CALL = {
  go: { label: "GO", icon: "✓", cls: "call--go" },
  caution: { label: "CAUTION", icon: "⚠", cls: "call--caution" },
  no_go: { label: "NO-GO", icon: "⛔", cls: "call--nogo" },
};

export default function ReportModal({ site, categories, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const dialog = useRef(null);

  const load = async (refresh = false) => {
    setLoading(true);
    setError("");
    try {
      setData(await api.report(site.site_id, refresh));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); dialog.current?.focus(); }, [site.site_id]);
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const r = data?.report;
  const call = r && CALL[r.recommendation?.value];
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={`Safety report for ${site.name}`}
           tabIndex={-1} ref={dialog} onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">SAFETY REPORT · {site.name}</p>
            {r && <h3>{r.headline}</h3>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>

        {loading && <p className="muted">Gathering facts and writing the report…</p>}
        {error && (
          <div className="error" role="alert">
            {error} <button className="link" onClick={() => load()}>Try again</button>
          </div>
        )}

        {r && !loading && (
          <div className="modal-body">
            <div className={`call ${call?.cls}`}>
              <span className="call-icon" aria-hidden="true">{call?.icon}</span>
              <div>
                <strong>{call?.label}</strong>
              </div>
            </div>

            <p>{r.summary}</p>

            {r.hazards?.length > 0 && (
              <section>
                <h4>Hazards</h4>
                <ul className="hazard-list">
                  {r.hazards.map((h, i) => (
                    <li key={i}>
                      <strong>{categoryLabel(h.category, categories)}</strong>{" "}
                      <span className={`badge badge--${h.level === "stop_work" ? "stop" : "warn"}`}>{h.level.replace("_", " ")}</span>
                      <p>{h.explanation}</p>
                      <span className="sources">{h.sources.map((s) => <abbr key={s} title={r.sources?.[s]}>{s}</abbr>)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <h4>Actions</h4>
              <ul>{r.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </section>

            <section>
              <h4>Uncertainty</h4>
              <p>{r.uncertainty}</p>
            </section>

            <details>
              <summary>Assumptions ({r.assumptions.length})</summary>
              <ul className="small">{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </details>
            <details>
              <summary>Sources</summary>
              <dl className="small sources-list">
                {Object.entries(r.sources || {}).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
              </dl>
            </details>

            <footer className="modal-foot small">
              <span>{data.generator.startsWith("gemini") ? `Written by ${data.generator.replace("gemini:", "Gemini ")}` : "Template report (AI unavailable or not configured)"}</span>
              <span className={data.verified ? "ok" : "warn"}>
                {data.verified ? "✓ Every number matches the facts" : `⚠ Numbers not in the facts: ${data.unverified_numbers.join(", ")}`}
              </span>
              <span>{new Date(data.created_at).toLocaleString()}</span>
              <button className="link" onClick={() => load(true)}>Regenerate</button>
            </footer>
          </div>
        )}
      </div>
    </div>
  );
}

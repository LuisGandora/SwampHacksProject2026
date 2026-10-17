import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { categoryLabel } from "../hazard.js";

const LEVEL = { high: "badge--stop", medium: "badge--warn", low: "badge--live" };

export default function ComplianceModal({ site, categories, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const dialog = useRef(null);

  const load = async (refresh = false) => {
    setLoading(true);
    setError("");
    try {
      setData(await api.compliance(site.site_id, refresh));
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
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={`Compliance next steps for ${site.name}`}
           tabIndex={-1} ref={dialog} onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">COMPLIANCE · {site.name}</p>
            {r && <h3>{r.headline}</h3>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>
        {loading && <p className="muted">Reviewing OSHA reports nearby and open crew reports…</p>}
        {error && <div className="error" role="alert">{error} <button className="link" onClick={() => load()}>Try again</button></div>}

        {r && !loading && (
          <div className="modal-body">
            <p>{r.summary}</p>
            {r.serious_open_reports > 0 && (
              <div className="call call--nogo">
                <span className="call-icon" aria-hidden="true">⏱</span>
                <div><strong>Serious injury reported?</strong><p className="small">{r.reporting_requirement}</p></div>
              </div>
            )}

            {r.priorities.length === 0 && <p className="muted">No OSHA reports or open crew reports near this site.</p>}
            <ol className="priority-list">
              {r.priorities.map((p) => (
                <li key={p.category}>
                  <div className="priority-head">
                    <span className={`badge ${LEVEL[p.priority]}`}>{p.priority.toUpperCase()}</span>
                    <strong>{p.title || categoryLabel(p.category, categories)}</strong>
                  </div>
                  <p className="small">{p.why}</p>
                  <ul>{p.next_steps.map((s, i) => <li key={i}>{s}</li>)}</ul>
                  <p className="small muted">Standards: {p.standards.join(" · ")}</p>
                  <p className="small muted">
                    Nearby OSHA reports: {p.counts.osha_last_12_months} in the latest 12 months of data,
                    {" "}{p.counts.osha_all_time} since 2015 · open crew reports: {p.counts.open_crew_reports}
                    <span className="sources">{p.sources.map((s) => <abbr key={s}>{s}</abbr>)}</span>
                  </p>
                </li>
              ))}
            </ol>

            <section>
              <h4>Before the shift</h4>
              <ul>{r.supervisor_checklist.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </section>

            {r.osha_examples?.length > 0 && (
              <details>
                <summary>Recent OSHA reports nearby ({r.osha_examples.length})</summary>
                <ul className="small">
                  {r.osha_examples.map((e) => (
                    <li key={`${e.category}${e.report_id}`}>
                      <strong>{e.date}</strong> · {e.employer}, {e.city} · {e.event_title}
                      {e.amputation > 0 && " · amputation"}{e.loss_of_eye > 0 && " · loss of eye"}
                      {e.hospitalized > 0 && " · hospitalized"} · {Number(e.distance_mi).toFixed(1)} mi away
                      {e.inspection_id && ` · OSHA inspection ${e.inspection_id}`}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <details>
              <summary>How priorities are set</summary>
              <p className="small">{r.priority_rule}. {data.facts?.time_windows}.</p>
              <p className="small">{r.reporting_requirement}</p>
            </details>

            <footer className="modal-foot small">
              <span>{data.generator.startsWith("gemini") ? `Written by ${data.generator.replace("gemini:", "Gemini ")}` : "Template (AI unavailable or not configured)"}</span>
              <span className={data.verified ? "ok" : "warn"}>
                {data.verified ? "✓ Every number matches the facts" : `⚠ Numbers not in the facts: ${data.unverified_numbers.join(", ")}`}
              </span>
              <span>{r.disclaimer}</span>
              <button className="link" onClick={() => load(true)}>Regenerate</button>
            </footer>
          </div>
        )}
      </div>
    </div>
  );
}

import { categoryLabel, countdown } from "../hazard.js";

export default function AlertsPanel({ alerts, categories, now, onSelect, notifyOn, onToggleNotify }) {
  const sorted = [...alerts].sort((a, b) =>
    (a.level === b.level ? 0 : a.level === "stop_work" ? -1 : 1) || new Date(b.last_trigger_at) - new Date(a.last_trigger_at));
  const stops = alerts.filter((a) => a.level === "stop_work").length;

  return (
    <section className="panel alerts-panel" aria-label="Active alerts">
      <header className="panel-head">
        <h2>Live alerts</h2>
        <span className="muted small">{stops} stop work · {alerts.length - stops} warnings</span>
      </header>
      {"Notification" in window && (
        <button className={`link small ${notifyOn ? "ok" : ""}`} onClick={onToggleNotify}>
          {notifyOn ? "🔔 Desktop alerts on" : "🔕 Turn on desktop alerts for stop-work"}
        </button>
      )}
      {sorted.length === 0 ? <p className="muted small">No active alerts.</p> : (
        <ul className="alert-list">
          {sorted.map((a) => {
            const left = countdown(a.all_clear_at, now.getTime());
            return (
              <li key={a.alert_id}>
                <button className={`alert alert--${a.level}`} onClick={() => onSelect(a.site_id)}>
                  <span className="alert-icon" aria-hidden="true">{a.level === "stop_work" ? "⛔" : "⚠"}</span>
                  <span className="alert-main">
                    <strong>{a.site_name}</strong>
                    <span className="small">{a.level === "stop_work" ? "STOP WORK" : "Warning"} · {categoryLabel(a.category, categories)}</span>
                    <span className="small muted">{a.message}</span>
                    {left && <span className="small mono">All-clear in {left}</span>}
                  </span>
                  {a.image_url && <img src={a.image_url} alt="" className="thumb" loading="lazy" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

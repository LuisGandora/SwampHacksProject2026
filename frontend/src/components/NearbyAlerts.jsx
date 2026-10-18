import { nearbyAlerts } from "../geo.js";
import { alertTiming, categoryLabel } from "../hazard.js";

export default function NearbyAlerts({ location, alerts, categories, now, reliable, lastUpdated, onSelect }) {
  const nearby = nearbyAlerts(alerts, location, now);
  const current = !!reliable && lastUpdated != null && now.getTime() - lastUpdated < 45000;
  return (
    <section aria-label="Live alerts within 10 miles">
      <h4>Live alerts within 10 miles</h4>
      <p className={`small ${current ? "muted" : "warn"}`} role="status">
        {lastUpdated == null ? "Waiting for live alert data."
          : !current ? `${nearby.length} nearby alert${nearby.length === 1 ? "" : "s"} in the last received data; live status is unverified.`
          : nearby.length ? `${nearby.length} active alert${nearby.length === 1 ? "" : "s"} within 10 miles.`
          : "No active site alerts reported within 10 miles."}
      </p>
      <p className="small muted">Distances are from this location to the affected site, not the hazard itself.
        {lastUpdated != null && ` Updated ${new Date(lastUpdated).toLocaleTimeString()}.`}</p>
      {nearby.length > 0 && <ul className="alert-list">
        {nearby.map((a) => (
          <li key={a.alert_id}>
            <button className={`alert alert--${a.level}`} onClick={() => onSelect(a.site_id)}>
              <span className="alert-icon" aria-hidden="true">{a.level === "stop_work" ? "⛔" : "⚠"}</span>
              <span className="alert-main">
                <strong>{a.level === "stop_work" ? "STOP WORK" : "Warning"} · {categoryLabel(a.category, categories)}</strong>
                <span className="small">{a.site_name} · {a.distance_mi < 0.1 ? "<0.1" : a.distance_mi.toFixed(1)} mi to alert site</span>
                <span className="small muted">{a.message}</span>
                {alertTiming(a, now.getTime()) && <span className="small mono">{alertTiming(a, now.getTime())}</span>}
              </span>
              {a.image_url && <img src={a.image_url} alt="" className="thumb" loading="lazy" />}
            </button>
          </li>
        ))}
      </ul>}
    </section>
  );
}

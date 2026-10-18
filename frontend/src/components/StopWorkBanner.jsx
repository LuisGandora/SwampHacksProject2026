import { useEffect, useRef, useState } from "react";
import { alertTiming, categoryLabel, countdown } from "../hazard.js";

// The brief's #1 rule, impossible to miss: a full-width STOP WORK screen with the all-clear countdown, which flashes
// "restarted" whenever a new strike pushes the all-clear back. Shows the selected site's stop-work alert if it has
// one, else the newest. Minimizing hides it until that alert restarts or another site stops work.
export default function StopWorkBanner({ alerts, selectedId, now, categories, onSelect, reliable = false }) {
  const stops = alerts.filter((a) => a.level === "stop_work")
    .sort((a, b) => new Date(b.last_trigger_at) - new Date(a.last_trigger_at));
  const a = stops.find((x) => x.site_id === selectedId) || stops[0];
  const [hidden, setHidden] = useState(null);      // "alert_id@all_clear_at" the user minimized
  const [restarted, setRestarted] = useState(false);
  const lastClear = useRef({});

  useEffect(() => {  // detect a countdown restart: the same alert's all-clear moved later
    stops.forEach((x) => {
      const prev = lastClear.current[x.alert_id];
      if (x.category === "lightning" && x.alert_id === a?.alert_id && prev && x.all_clear_at && new Date(x.all_clear_at) > new Date(prev)) {
        setRestarted(true);
        setTimeout(() => setRestarted(false), 6000);
        navigator.vibrate?.([300, 100, 300]);
      }
      lastClear.current[x.alert_id] = x.all_clear_at;
    });
  }, [alerts]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!a || hidden === `${a.alert_id}@${a.all_clear_at}`) return null;
  const left = countdown(a.all_clear_at, now.getTime());
  const total = a.all_clear_at && (new Date(a.all_clear_at) - new Date(a.last_trigger_at)) / 1000;
  const remaining = a.all_clear_at ? Math.max(0, (new Date(a.all_clear_at) - now) / 1000) : null;
  const expired = a.all_clear_at && new Date(a.all_clear_at).getTime() <= now.getTime();
  const others = stops.filter((x) => x.site_id !== a.site_id);

  return (
    <section className={`stopwork ${restarted && a.category === "lightning" ? "stopwork--restart" : ""}`}
             aria-label="Stop-work alert">
      <div className="stopwork-main">
        <p className="stopwork-title" role="alert">
          {`⛔ STOP WORK · ${categoryLabel(a.category, categories).toUpperCase()}`}
        </p>
        <button className="link stopwork-site" onClick={() => onSelect(a.site_id)}>{a.site_name}</button>
        <p className="small">{a.message}</p>
        {!reliable && <p className="warn small">Live alert data is unverified. A countdown cannot confirm all-clear.</p>}
        {restarted && a.category === "lightning" && !expired && <p className="stopwork-flash">⚡ New strike: countdown restarted</p>}
        {expired && <p className="warn small">Alert timer ended. Await an updated alert list and verify conditions before resuming.</p>}
      </div>
      <div className="stopwork-clock">
        <span className="small">{a.category === "lightning" ? "LIGHTNING HOLD" : "ALERT STATUS"}</span>
        {a.category === "lightning"
          ? <strong className="mono">{expired ? "Awaiting fresh data" : left || "Expiry unavailable"}</strong>
          : <span className="small mono">{expired ? "Awaiting fresh data" : alertTiming(a, now.getTime()) || "Expiry unavailable"}</span>}
        {a.category === "lightning" && total > 0 && <span className="stopwork-bar"><span style={{ width: `${Math.min(100, (remaining / total) * 100)}%` }} /></span>}
        {a.category === "lightning" && !expired && <span className="small muted">Restarts on every strike within range; verify conditions before resuming</span>}
      </div>
      <div className="stopwork-side">
        {others.length > 0 && (
          <button className="small" onClick={() => onSelect(others[0].site_id)}>+{others.length} more site{others.length > 1 ? "s" : ""}</button>
        )}
        <button className="icon-btn" onClick={() => setHidden(`${a.alert_id}@${a.all_clear_at}`)} aria-label="Minimize">▁</button>
      </div>
    </section>
  );
}

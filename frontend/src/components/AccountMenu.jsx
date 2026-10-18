import { useEffect, useState } from "react";
import { api } from "../api.js";
import { AUTH0 } from "../auth.js";
import { categoryLabel } from "../hazard.js";

// Account popup from the avatar: who you are, password reset, your saved locations and the reports you've sent.
export default function AccountMenu({ auth, categories, saved, onSavedChange, onGoTo, onClose, identity, onIdentity, onBriefing }) {
  const [me, setMe] = useState(null);
  const [reports, setReports] = useState(null);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(null);   // "saved" | "reports" | null: which list is expanded
  const u = auth.user || {};
  const passwordAccount = (u.sub || "").startsWith("auth0|");  // Google etc. manage passwords themselves

  useEffect(() => {  // per identity: who you are acting as, and the reports sent as them
    setReports(null);
    api.me().then(setMe).catch(() => {});
    api.myReports().then(setReports).catch((e) => setMsg(e.message));
  }, [identity]);
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const resetPassword = async () => {
    setMsg("Sending…");
    try {  // Auth0 emails a reset link; the password never passes through Planet Control
      const r = await fetch(`https://${AUTH0.domain}/dbconnections/change_password`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: AUTH0.clientId, email: u.email, connection: "Username-Password-Authentication" }),
      });
      setMsg(r.ok ? `Check ${u.email} for a password reset link.` : `Couldn't send the reset email (${r.status}).`);
    } catch {
      setMsg("Couldn't reach Auth0 to send the reset email.");
    }
  };
  const deleteReports = async (ids) => {
    if (!window.confirm(ids.length > 1 ? `Delete all ${ids.length} of your reports? This can't be undone.` : "Delete this report?")) return;
    try {
      for (const id of ids) await api.deleteCrewReport(id).catch((e) => { if (e.status !== 404) throw e; });
      setReports((list) => list.filter((r) => !ids.includes(r.report_id)));
    } catch (e) { setMsg(e.message); }
  };
  const run = (fn) => async () => { try { setMsg(""); await fn(); } catch (e) { if (e.status !== 404) setMsg(e.message); } onSavedChange(); };
  const section = (key, label, count) => (
    <button className={`section-toggle ${open === key ? "on" : ""}`} aria-expanded={open === key}
            onClick={() => setOpen(open === key ? null : key)}>
      <span>{label}</span><span className="muted">{count ?? "…"} {open === key ? "▴" : "▾"}</span>
    </button>);

  return (
    <section className="account-menu panel" role="dialog" aria-label="Your account">
      <header className="panel-head">
        <div className="account-who">
          {u.picture && <img src={u.picture} alt="" />}
          <div>
            <strong>{u.name || u.email}</strong>
            <p className="small muted">{u.email}{me ? ` · since ${new Date(me.created_at).toLocaleDateString()}` : ""}</p>
          </div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close account">✕</button>
      </header>

      <div className="identity-switch">
        <span className="small">Acting as</span>
        <div className="seg" role="group" aria-label="Identity">
          {[["crew", "👷 Crew"], ["supervisor", "🧭 Supervisor"]].map(([k, label]) => (
            <button key={k} className={identity === k ? "on" : ""} aria-pressed={identity === k} onClick={() => onIdentity(k)}>{label}</button>
          ))}
        </div>
      </div>
      {identity === "supervisor" && me && !me.supervisor_approved && (
        <p className="small warn">Supervisor actions (closing others' reports, demo) need an admin's approval. Your Supervisor
          saved locations and channels work now.</p>
      )}
      <div className="actions">
        {passwordAccount
          ? <button onClick={resetPassword}>🔑 Reset password</button>
          : <span className="small muted">You sign in with {(u.sub || "").split("|")[0].replace("-oauth2", "") || "a linked account"}: change your password there.</span>}
        {identity === "supervisor" && onBriefing && (
          <button onClick={onBriefing} title="The briefing written at 6 PM, replayed until the next one">📋 Replay 6 PM briefing</button>
        )}
        <button className="ghost" onClick={auth.logout}>Sign out</button>
      </div>
      {msg && <p className="small">{msg}</p>}

      {section("saved", "📌 Saved locations", saved?.length ?? 0)}
      {open === "saved" && (!saved?.length ? <p className="small muted">None yet. Use ☆ on a site, or ☆ Save on the 🧭 Location screen.</p> : (
        <ul className="account-list">
          {saved.map((l) => (
            <li key={l.location_id}>
              <button className="link" onClick={() => onGoTo(l)}>📌 {l.name}</button>
              <span className="small muted">{l.plants} plants · {Number(l.radius_mi)} mi</span>
              <button className="link small" onClick={run(() => api.locationBriefing(l.location_id))}>Preview 6 PM</button>
              <button className="icon-btn" aria-label={`Delete ${l.name}`} onClick={run(() => api.deleteLocation(l.location_id))}>✕</button>
              {l.briefing && <p className="small muted">6 PM, {l.briefing_date}: {l.briefing}</p>}
            </li>
          ))}
        </ul>
      ))}

      {section("reports", "📝 Your reports", reports?.length)}
      {open === "reports" && reports?.length === 0 && <p className="small muted">You haven't sent any crew reports yet.</p>}
      {open === "reports" && reports?.length > 0 && (
        <button className="link small" onClick={() => deleteReports(reports.map((r) => r.report_id))}>🗑 Clear all my reports</button>
      )}
      {open === "reports" && reports?.length > 0 && (
        <ul className="account-list">
          {reports.map((r) => (
            <li key={r.report_id}>
              <strong>{categoryLabel(r.category, categories)}</strong>
              <span className={`badge ${r.status === "open" ? "badge--warn" : "badge--live"}`}>{r.status.toUpperCase()}</span>
              <span className="small muted">{r.site_name || "no site nearby"} · {new Date(r.reported_at).toLocaleString()}</span>
              <button className="icon-btn" aria-label="Delete this report" onClick={() => deleteReports([r.report_id])}>✕</button>
              {r.description && <p className="small">{r.description}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

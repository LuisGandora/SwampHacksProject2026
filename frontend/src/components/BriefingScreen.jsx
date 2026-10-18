import { useEffect, useState } from "react";
import { api } from "../api.js";
import { band, fmtScore, replayNote, usd } from "../hazard.js";

const TABS = [["start", "Right now"], ["6pm", "6 PM · Tomorrow"]];

// Full-screen briefing across your starting area and saved locations (Gemini, template fallback). Opens when you
// pick a location, and for supervisors at 6 PM. Supervisors get a drafted message per Discord/Slack channel for the
// crews who read it: edit it, then Send. "Run again" rebuilds it from fresh data.
export default function BriefingScreen({ focus, kind, identity, onSelect, onClose, onNotify, toast }) {
  const [tab, setTab] = useState(kind);
  const [data, setData] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [drafts, setDrafts] = useState({});   // channel_id -> edited text
  const [sent, setSent] = useState({});
  const where = focus?.all || !focus ? {} : { lat: focus.lat, lon: focus.lon, radius_mi: focus.radius, label: focus.label };

  const load = (k, refresh = false) => {
    setBusy(k); setError("");
    api.myBriefing({ ...where, kind: k, refresh })
      .then((b) => { setData((d) => ({ ...d, [k]: b })); setDrafts({}); setSent({}); })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(""));
  };
  useEffect(() => { setTab(kind); }, [kind]);
  useEffect(() => { if (!data[tab]) load(tab); }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const b = data[tab];
  const r = b?.report;
  const facts = b?.facts;
  const send = async (m) => {
    const text = drafts[m.channel_id] ?? m.text;
    setBusy(`send${m.channel_id}`);
    try { await api.sendToChannel(m.channel_id, text); setSent((s) => ({ ...s, [m.channel_id]: true })); toast(`Sent to ${m.audience}.`); }
    catch (e) { toast(e.message, "error"); }
    setBusy("");
  };
  const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast("Copied."), () => toast("Couldn't copy.", "error"));
  const go = (id) => { onSelect(id); onClose(); };

  return (
    <div className="briefing-screen" role="dialog" aria-modal="true" aria-label="Your briefing">
      <div className="briefing-inner">
        <header className="briefing-head">
          <div>
            <p className="eyebrow">{tab === "6pm" ? `6 PM BRIEFING · FOR ${(facts?.tomorrow || "TOMORROW").toUpperCase()}`
                                                   : `YOUR BRIEFING · ${facts?.local_time || ""}`}</p>
            <h2>{r?.headline || (busy ? "Writing your briefing…" : "Your briefing")}</h2>
          </div>
          <div className="briefing-actions">
            <div className="seg" role="tablist">
              {TABS.map(([k, label]) => (
                <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
              ))}
            </div>
            <button onClick={() => load(tab, true)} disabled={!!busy} title="Rebuild from fresh data">↻ Run again</button>
            <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </header>

        {error && <p className="error">{error}</p>}
        {busy === tab && <p className="muted">Gathering your areas, alerts and tomorrow's forecast; Gemini is writing it up (up to a minute)…</p>}

        {r && (
          <>
            <p className="briefing-summary">{r.summary}</p>
            <div className="briefing-cols">
              <section>
                <h4>Do first</h4>
                <ol className="plan-list">{r.priorities.map((p, i) => <li key={i}><p>{p}</p></li>)}</ol>
              </section>
              {identity === "supervisor" && (
                <section>
                  <h4>Messages for your crews</h4>
                  {r.crew_messages.length === 0 ? (
                    <p className="small muted">Add your crews' Discord or Slack channel under{" "}
                      <button className="link small" onClick={onNotify}>🔔 Notifications</button> (as Supervisor), then Run again:
                      you'll get a drafted message for each channel, covering its sites.</p>
                  ) : r.crew_messages.map((m) => (
                    <div key={m.channel_id} className="crew-msg">
                      <p className="small"><strong>{m.audience}</strong> <span className="muted">· {m.kind}</span></p>
                      <textarea rows={5} maxLength={1900} value={drafts[m.channel_id] ?? m.text}
                                onChange={(e) => setDrafts((d) => ({ ...d, [m.channel_id]: e.target.value }))} />
                      <div className="row-actions">
                        <button className="primary" disabled={!!busy || sent[m.channel_id]} onClick={() => send(m)}>
                          {sent[m.channel_id] ? "✓ Sent" : busy === `send${m.channel_id}` ? "Sending…" : `Send to ${m.audience}`}</button>
                        <button onClick={() => copy(drafts[m.channel_id] ?? m.text)}>Copy</button>
                      </div>
                    </div>
                  ))}
                </section>
              )}
            </div>

            <h4>Your areas</h4>
            <div className="briefing-areas">
              {facts.areas.map((a, i) => (
                <article key={a.area} className="panel">
                  <h3>{a.area}</h3>
                  <p className="small mono">
                    {a.plants_in_range} plants · <span className="stop">{a.stop_work_now} stop</span> ·{" "}
                    <span className="warn">{a.warnings_now} warn</span> · {a.high_tomorrow} high tomorrow
                    {a.open_crew_reports > 0 && ` · ${a.open_crew_reports} crew reports`}</p>
                  {a.usd_at_risk_tomorrow > 0 && (
                    <p className="small">≈ <strong>{a.crew_hours_at_risk_tomorrow} crew-hours ({usd(a.usd_at_risk_tomorrow)})</strong> at
                      risk tomorrow <span className="muted">(estimate)</span></p>)}
                  {r.areas[i]?.summary && <p className="small">{r.areas[i].summary}</p>}
                  <ul className="briefing-plants">
                    {a.top_plants.map((p) => (
                      <li key={p.site_id}>
                        <i className="dot" style={{ background: band(Number(p.score_tomorrow ?? 0)).color }} />
                        <button className="link small" onClick={() => go(p.site_id)}>{p.name}</button>
                        <span className="small muted">{fmtScore(p.score_tomorrow)} tmrw{p.top_hazard_tomorrow ? ` · ${p.top_hazard_tomorrow.replace("_", " ")}` : ""}</span>
                        {p.stop_work_now && <span className="badge badge--stop">STOP</span>}
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
            <p className="small muted">{r.uncertainty}</p>
            <footer className="modal-foot small">
              <span>{b.generator.startsWith("gemini") ? `Written by ${b.generator.replace("gemini:", "")} from the facts above`
                                                     : "Template (Gemini unavailable)"}
                {b.verified ? " · numbers checked against the data" : ` · unchecked numbers: ${b.unverified_numbers.join(", ")}`}</span>
              <span>{replayNote(b)}</span>
            </footer>
          </>
        )}
      </div>
    </div>
  );
}

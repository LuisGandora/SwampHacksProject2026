import { useEffect, useState } from "react";
import { api } from "../api.js";

const LEVELS = [["stop_work", "Stop work"], ["warning", "Warnings"]];

// Your own Discord/Slack channels: what gets sent where. The webhook URL is stored encrypted on the server and
// never shown again (only its last 4 characters).
export default function NotifyModal({ sites, selected, onClose }) {
  const [channels, setChannels] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [form, setForm] = useState({ webhook_url: "", label: "", levels: ["stop_work"], briefings: true, crew_reports: true });
  const [scope, setScope] = useState("all");
  const [picked, setPicked] = useState(() => (selected ? [selected.site_id] : []));
  const [search, setSearch] = useState("");

  const load = () => api.channels().then(setChannels).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const act = (label, fn) => async (e) => {
    e?.preventDefault();
    setError(""); setBusy(label);
    try { await fn(); } catch (err) { setError(err.message); } finally { setBusy(""); }
  };
  const add = act("Saving…", async () => {
    await api.addChannel({ ...form, label: form.label.trim() || null, site_ids: scope === "all" ? null : picked });
    setForm((f) => ({ ...f, webhook_url: "", label: "" }));
    await load();
  });
  const toggleLevel = (k) => setForm((f) => ({ ...f, levels: f.levels.includes(k) ? f.levels.filter((x) => x !== k) : [...f.levels, k] }));
  const name = (id) => sites.find((s) => s.site_id === id)?.name || `Site ${id}`;
  const matches = search.trim().length < 2 ? [] : sites.filter((s) =>
    `${s.name} ${s.county} ${s.state}`.toLowerCase().includes(search.toLowerCase()) && !picked.includes(s.site_id)).slice(0, 8);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Notifications" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">NOTIFICATIONS</p>
            <h3>Send alerts to your Discord or Slack</h3>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>

        {channels === null ? <p className="muted">Loading…</p> : channels.length === 0 ? (
          <p className="muted small">No channels yet. Add one below.</p>
        ) : (
          <ul className="channel-list">
            {channels.map((c) => (
              <li key={c.channel_id}>
                <div>
                  <strong>{c.label || (c.kind === "discord" ? "Discord" : "Slack")}</strong>
                  <span className="muted small mono"> · {c.kind} …{c.webhook_hint}</span>
                  <p className="small">
                    {c.levels.map((l) => LEVELS.find(([k]) => k === l)?.[1]).join(" + ")}
                    {c.crew_reports && " · crew reports"}{c.briefings && " · 6 PM briefings"}
                    {" · "}{c.site_ids ? c.site_ids.map(name).join(", ") : "all sites"}
                  </p>
                  <p className={`small ${c.last_error ? "error" : "muted"}`}>
                    {c.last_error ? `Last send failed: ${c.last_error}` : c.last_sent_at ? `Last sent ${new Date(c.last_sent_at).toLocaleString()}` : "Nothing sent yet"}
                  </p>
                </div>
                <div className="row-actions">
                  <button className="link small" disabled={!!busy}
                          onClick={act("Testing…", async () => { await api.testChannel(c.channel_id); await load(); })}>Send test</button>
                  <button className="link small" disabled={!!busy}
                          onClick={act("Deleting…", async () => { await api.deleteChannel(c.channel_id); await load(); })}>Delete</button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <form className="crew-form channel-form" onSubmit={add}>
          <h4>Add a channel</h4>
          <label>Webhook URL
            <input type="url" required value={form.webhook_url} autoComplete="off" spellCheck={false}
                   onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
                   placeholder="https://discord.com/api/webhooks/… or https://hooks.slack.com/services/…" />
          </label>
          <p className="small muted">Discord: channel ⚙ → Integrations → Webhooks → New Webhook → Copy Webhook URL.
            Slack: create an app → Incoming Webhooks → Add New Webhook to Workspace.</p>
          <label>Name (optional)
            <input value={form.label} maxLength={80} onChange={(e) => setForm({ ...form, label: e.target.value })}
                   placeholder="e.g. Crew 4 Discord" />
          </label>
          <fieldset>
            <legend>Send</legend>
            <div className="checks">
              {LEVELS.map(([k, label]) => (
                <label key={k}><input type="checkbox" checked={form.levels.includes(k)} onChange={() => toggleLevel(k)} /> {label}</label>
              ))}
              <label><input type="checkbox" checked={form.crew_reports} onChange={(e) => setForm({ ...form, crew_reports: e.target.checked })} /> Crew reports</label>
              <label><input type="checkbox" checked={form.briefings} onChange={(e) => setForm({ ...form, briefings: e.target.checked })} /> 6 PM briefings</label>
            </div>
          </fieldset>
          <fieldset>
            <legend>For</legend>
            <div className="seg">
              <button type="button" className={scope === "all" ? "on" : ""} onClick={() => setScope("all")}>All sites</button>
              <button type="button" className={scope === "some" ? "on" : ""} onClick={() => setScope("some")}>Chosen sites</button>
            </div>
            {scope === "some" && (
              <div>
                <div className="chips">
                  {picked.map((id) => (
                    <button type="button" key={id} className="chip" onClick={() => setPicked(picked.filter((x) => x !== id))}
                            aria-label={`Remove ${name(id)}`}>{name(id)} ✕</button>
                  ))}
                </div>
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search sites by name, county or state" />
                {matches.map((s) => (
                  <button type="button" key={s.site_id} className="link small site-match"
                          onClick={() => { setPicked([...picked, s.site_id]); setSearch(""); }}>+ {s.name} ({s.county}, {s.state})</button>
                ))}
              </div>
            )}
          </fieldset>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="form-actions">
            <button type="submit" className="primary"
                    disabled={!!busy || !form.levels.length && !form.crew_reports && !form.briefings || (scope === "some" && !picked.length)}>
              {busy || "Add channel"}
            </button>
          </div>
        </form>
        <p className="small muted">The URL is encrypted on the server and never shown again. Delete the channel to change it.</p>
      </div>
    </div>
  );
}

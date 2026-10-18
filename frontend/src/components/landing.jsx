import { useEffect, useState } from "react";
import { api } from "../api.js";
import { sitesWithin } from "../geo.js";

// Intro: (1) optional sign-in, (2) where you're starting from: search a place or site, use GPS, or tap the globe
// behind this panel to drop a pin. No sites are drawn until a location is chosen.
export default function Landing({ sites = [], auth, place, setPlace, flyTo, onDone, reopened = false, identity, onIdentity }) {
  const first = auth.enabled && !auth.isAuthenticated && !reopened ? "welcome" : auth.isAuthenticated && !identity && !reopened ? "role" : "where";
  const [step, setStep] = useState(first);
  const [radius, setRadius] = useState(50);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [status, setStatus] = useState("");
  const [saved, setSaved] = useState(null);       // your saved locations (signed in only)
  const [savedOpen, setSavedOpen] = useState(false);
  const signedIn = auth.isAuthenticated && !auth.isLoading;
  useEffect(() => { if (signedIn && step === "where") api.locations().then(setSaved).catch(() => setSaved(null)); },
            [signedIn, step]);
  const act = (fn) => async () => { try { setStatus(""); await fn(); setSaved(await api.locations()); } catch (e) { setStatus(e.message); } };
  // ☆ next to a place: saves it with the current radius; its name is the place's first part ("Tampa", plant name).
  const saveLink = (p) => signedIn && (
    <button className="link small save-link" title="Save this location (it gets a 6 PM briefing)"
            onClick={act(() => api.saveLocation({ name: (p.label || "Saved location").split(/[,·]/)[0].trim().slice(0, 80),
                                                  lat: p.lat, lon: p.lon, radius_mi: radius, site_id: p.site_id ?? null }))}>☆ Save</button>);
  // Coming back from Auth0, the sign-in finishes after the first render: move on once it does.
  useEffect(() => { if (auth.isAuthenticated && step === "welcome") setStep(identity ? "where" : "role"); },
            [auth.isAuthenticated]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (p) => { setPlace(p); setResults(null); flyTo(p.lon, p.lat); };

  const search = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (q.length < 2) return;
    const local = sites.filter((s) => `${s.name} ${s.county} ${s.state}`.toLowerCase().includes(q.toLowerCase()))
      .slice(0, 4).map((s) => ({ label: `${s.name} · power plant, ${s.county}, ${s.state}`, lat: s.lat, lon: s.lon, site_id: s.site_id }));
    setStatus("Searching…");
    try {  // OpenStreetMap place search (Nominatim): one request per search, US only
      const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=us&limit=5&q=${encodeURIComponent(q)}`);
      const places = (await r.json()).map((p) => ({ label: p.display_name, lat: Number(p.lat), lon: Number(p.lon) }));
      setResults([...local, ...places]);
      setStatus(local.length + places.length ? "" : "No matches. Try a city, county or ZIP, or tap the globe.");
    } catch {
      setResults(local);
      setStatus(local.length ? "" : "Place search is unavailable. Tap the globe to drop a pin instead.");
    }
  };

  const gps = () => {
    if (!navigator.geolocation) return setStatus("This browser can't share location. Search or tap the globe instead.");
    setStatus("Finding your location…");
    navigator.geolocation.getCurrentPosition(
      (pos) => { choose({ label: "Your location", lat: pos.coords.latitude, lon: pos.coords.longitude }); setStatus(""); },
      (err) => setStatus(`Couldn't get your location (${err.message}). Search or tap the globe instead.`),
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 });
  };

  const count = place ? sitesWithin(sites, { ...place, radius }).length : 0;

  if (step === "welcome") return (
    <div className="landing">
      <p className="eyebrow">PLANET CONTROL</p>
      <h1>Field hazard operations</h1>
      <p className="landing-intro">Live hazard levels, stop-work alerts and crew reports for power plant sites in Florida,
        Georgia and Alabama.</p>
      <p className="small muted">Signing in lets you send crew reports and get alerts in your team's Discord or Slack.
        You can look around without it.</p>
      {auth.error && <p className="error small" role="alert">Sign-in failed: {auth.error.message}</p>}
      <div className="landing-actions">
        <button className="landing-go" onClick={auth.login} disabled={auth.isLoading}>
          {auth.isLoading ? "Checking sign-in…" : "Sign in"}</button>
        <button className="landing-skip" onClick={() => setStep("where")}>Continue without signing in</button>
      </div>
    </div>
  );

  if (step === "role") return (
    <div className="landing">
      <p className="eyebrow">SIGNED IN{auth.user?.name ? ` · ${auth.user.name}` : ""}</p>
      <h1>How are you working today?</h1>
      <div className="role-choices">
        <button className="role-choice" onClick={() => { onIdentity("crew"); setStep("where"); }}>
          <strong>👷 Crew</strong>
          <span className="small muted">In the field: report hazards with photos, get stop-work alerts for your sites.</span>
        </button>
        <button className="role-choice" onClick={() => { onIdentity("supervisor"); setStep("where"); }}>
          <strong>🧭 Supervisor</strong>
          <span className="small muted">Rank sites, close crew reports, run compliance and briefings.</span>
        </button>
      </div>
      <p className="small muted">Both? Pick one now and switch anytime from your profile. Each keeps its own saved locations,
        Discord/Slack channels and reports. Supervisor actions need an admin's approval.</p>
    </div>
  );

  return (
    <div className="landing">
      <p className="eyebrow">STEP 2 OF 2{auth.isAuthenticated && auth.user?.name ? ` · ${auth.user.name}` : ""}{auth.isAuthenticated && identity ? ` · ${identity}` : ""}</p>
      <h1>Where are you starting from?</h1>
      <form className="landing-search" onSubmit={search}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="City, county, ZIP or plant name"
               aria-label="Search for a place or power plant" />
        <button type="submit">Search</button>
        {signedIn && (
          <button type="button" onClick={() => setSavedOpen((v) => !v)} aria-expanded={savedOpen}
                  title="Your saved locations">📌 Saved {savedOpen ? "▴" : "▾"}</button>
        )}
      </form>
      {savedOpen && (
        <ul className="saved-dropdown" aria-label="Your saved locations">
          {!saved?.length ? <li className="muted small">No saved locations yet. Use ☆ Save on a place or ☆ on a site card.</li>
            : saved.map((l) => (
              <li key={l.location_id}>
                <button className="link" onClick={() => onDone({ lat: l.lat, lon: l.lon, radius: Number(l.radius_mi) })}>
                  📌 {l.name}</button>
                <span className="muted small"> {l.plants} plants · {Number(l.radius_mi)} mi</span>
              </li>
            ))}
        </ul>
      )}
      {results?.length > 0 && (
        <ul className="landing-results">
          {results.map((r) => (
            <li key={`${r.lat},${r.lon},${r.label}`}>
              <button className="link" onClick={() => choose(r)}>{r.label}</button> {saveLink(r)}
            </li>
          ))}
        </ul>
      )}
      <p className="small">
        <button className="link" onClick={gps}>📍 Use my location</button>
        <span className="muted"> · or tap anywhere on the globe to drop a pin</span>
      </p>
      {status && <p className="landing-status">{status}</p>}

      {place && (
        <div className="radius-section">
          <p className="small">Starting point: <strong>{place.label || `${place.lat.toFixed(3)}, ${place.lon.toFixed(3)}`}</strong>
            {" "}{saveLink(place)}</p>
          <label htmlFor="radius">Show power plants within <strong>{radius} miles</strong></label>
          <input id="radius" type="range" min="5" max="200" step="5" value={radius} onChange={(e) => setRadius(Number(e.target.value))} />
          <p className="landing-status">{count} power plant{count === 1 ? "" : "s"} in range</p>
          <p className="small muted">{signedIn ? "☆ Save keeps a place for later, with a 6 PM briefing for the plants in range."
            : "Sign in to save places for later and get their 6 PM briefing."}</p>
        </div>
      )}

      <div className="landing-actions">
        <button className="landing-go" disabled={!place} onClick={() => onDone({ ...place, radius })}>Show power plants</button>
        <button className="landing-skip" onClick={() => onDone({ all: true })}>Show all sites</button>
      </div>
    </div>
  );
}

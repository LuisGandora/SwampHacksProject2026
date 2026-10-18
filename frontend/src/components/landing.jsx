import { useEffect, useState } from "react";
import { sitesWithin } from "../geo.js";

// Intro: (1) optional sign-in, (2) where you're starting from: search a place or site, use GPS, or tap the globe
// behind this panel to drop a pin. No sites are drawn until a location is chosen.
export default function Landing({ sites = [], auth, place, setPlace, flyTo, onDone }) {
  const [step, setStep] = useState(auth.enabled && !auth.isAuthenticated ? "welcome" : "where");
  const [radius, setRadius] = useState(50);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [status, setStatus] = useState("");
  // Coming back from Auth0, the sign-in finishes after the first render: move on once it does.
  useEffect(() => { if (auth.isAuthenticated) setStep("where"); }, [auth.isAuthenticated]);

  const choose = (p) => { setPlace(p); setResults(null); flyTo(p.lon, p.lat); };

  const search = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (q.length < 2) return;
    const local = sites.filter((s) => `${s.name} ${s.county} ${s.state}`.toLowerCase().includes(q.toLowerCase()))
      .slice(0, 4).map((s) => ({ label: `${s.name} · power plant, ${s.county}, ${s.state}`, lat: s.lat, lon: s.lon }));
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

  return (
    <div className="landing">
      <p className="eyebrow">STEP 2 OF 2{auth.isAuthenticated && auth.user?.name ? ` · signed in as ${auth.user.name}` : ""}</p>
      <h1>Where are you starting from?</h1>
      <form className="landing-search" onSubmit={search}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="City, county, ZIP or plant name"
               aria-label="Search for a place or power plant" />
        <button type="submit">Search</button>
      </form>
      {results?.length > 0 && (
        <ul className="landing-results">
          {results.map((r) => <li key={`${r.lat},${r.lon},${r.label}`}><button className="link" onClick={() => choose(r)}>{r.label}</button></li>)}
        </ul>
      )}
      <p className="small">
        <button className="link" onClick={gps}>📍 Use my location</button>
        <span className="muted"> · or tap anywhere on the globe to drop a pin</span>
      </p>
      {status && <p className="landing-status">{status}</p>}

      {place && (
        <div className="radius-section">
          <p className="small">Starting point: <strong>{place.label || `${place.lat.toFixed(3)}, ${place.lon.toFixed(3)}`}</strong></p>
          <label htmlFor="radius">Show power plants within <strong>{radius} miles</strong></label>
          <input id="radius" type="range" min="5" max="200" step="5" value={radius} onChange={(e) => setRadius(Number(e.target.value))} />
          <p className="landing-status">{count} power plant{count === 1 ? "" : "s"} in range</p>
        </div>
      )}

      <div className="landing-actions">
        <button className="landing-go" disabled={!place} onClick={() => onDone({ ...place, radius })}>Show power plants</button>
        <button className="landing-skip" onClick={() => onDone({ all: true })}>Skip and show everything</button>
      </div>
    </div>
  );
}

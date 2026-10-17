<<<<<<< Updated upstream
import { useState, useEffect } from "react";
import { CITIES, MAP_SIZE, latLonToPixel } from "./data.js";
=======
import { useCallback, useEffect, useRef, useState } from "react";
import { API, api } from "./api.js";
import AlertsPanel from "./components/AlertsPanel.jsx";
import CrewReportForm from "./components/CrewReportForm.jsx";
import GlobeMap from "./components/GlobeMap.jsx";
import RankingPanel from "./components/RankingPanel.jsx";
import ReportModal from "./components/ReportModal.jsx";
import SiteCard from "./components/SiteCard.jsx";
import { categoryLabel } from "./hazard.js";
import Landing from "./components/landing.jsx";
import { sitesWithin } from "./geo.js";
>>>>>>> Stashed changes

// Green (safe) -> Yellow-ish (in the middle) ->  red (big hazard) for a 0-10 index.
const hazardColor = (v) => `hsl(${120 - v * 12}, 70%, 42%)`;

export default function App() {
  const [me, setMe] = useState(null);
<<<<<<< Updated upstream
  const [locError, setLocError] = useState("");
  const [now, setNow] = useState(new Date());
=======
  const [sensor, setSensor] = useState("normal");
  const [aiSite, setAiSite] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [tab, setTab] = useState("rank");
  const [notifyOn, setNotifyOn] = useState(() => "Notification" in window && Notification.permission === "granted");
  const [refreshKey, setRefreshKey] = useState(0);
  const seenAlerts = useRef(null);
  const globe = useRef(null);
  const [focus, setFocus] = useState(null); // null = landing page still showing
>>>>>>> Stashed changes

  useEffect(() => {
  if (!navigator.geolocation) {
    setLocError("Location is not supported in this browser.");
    return;
  }
  const watchId = navigator.geolocation.watchPosition(
    (pos) => setMe({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
    (err) => setLocError(err.message)
  );
  
  const id = setInterval(() => setNow(new Date()), 1000);

  return () => navigator.geolocation.clearWatch(watchId);
  }, []);

  const myPixel = me ? latLonToPixel(me.lat, me.lon) : null;
  const onMap =
  myPixel &&
  myPixel.x >= 0 && myPixel.x <= MAP_SIZE.width &&
  myPixel.y >= 0 && myPixel.y <= MAP_SIZE.height;

  const [cities, setCities] = useState(CITIES);
  const [selectedName, setSelectedName] = useState(null);
  const [rankOpen, setRankOpen] = useState(false);

  const [reports, setReports] = useState({});
  const [reportOpen, setReportOpen] = useState(false);
  const [reportType, setReportType] = useState("Wildlife");
  const [reportText, setReportText] = useState("");

  const selected = cities.find((c) => c.name === selectedName) ?? null;

  const siteReports = selected ? reports[selected.name] ?? [] : [];

  const submitReport = (e) => {
    e.preventDefault();
    const text = reportText.trim();
    if (!text || !selected) return;
    const entry = {
      id: Date.now(),
      type: reportType,
      text,
      time: new Date().toISOString(),
    };
    setReports((prev) => ({
      ...prev,
      [selected.name]: [entry, ...(prev[selected.name] ?? [])], // newest first
    }));
    setReportText("");
    setReportOpen(false);
  };

  const setHazard = (value) =>
    setCities((prev) =>
      prev.map((c) => (c.name === selectedName ? { ...c, hazard: value } : c))
    );

  const visibleSites = sitesWithin(sites, focus);
  const globeRef = useRef(null);

  const handleLandingDone = (f) => {
    setFocus(f);
    if (f.all) {
      globeRef.current?.flyHome();
    } else {
      setMe({ lat: f.lat, lon: f.lon });
      globeRef.current?.flyRadius(f.lon, f.lat, f.radius);
    }
  };

// ...
<Landing sites={sites} onDone={handleLandingDone} />

  return (
<<<<<<< Updated upstream
    <div className="page">
      <header className="header">
        <h1 className="title">Plan It By Planet</h1>
        <p className="location">
        {me
          ? `Your location: ${me.lat.toFixed(3)}, ${me.lon.toFixed(3)}`
          : locError || "Finding your location..."}
        </p>
        <p className="clock">{now.toLocaleTimeString()}</p>
        <p className="index">
          Hazard Index:{" "}
          {selected ? (
            <strong style={{ color: hazardColor(selected.hazard) }}>
              {selected.hazard} / 10
            </strong>
          ) : (
            "0 - 10"
          )}
        </p>
=======
    <div className="app">
      <GlobeMap ref={globe} sites={visibleSites} focus={focus} lightning={lightning} selectedId={selectedId} onSelect={select}
                pinMode={pinMode} pin={pin} onPin={(p) => { setPin(p); setPinMode(false); setTab("site"); }}
                me={me} sensor={sensor} />
      {sensor === "crt" && <div className="scanlines" aria-hidden="true" />}

      <header className="hud">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">◎</span>
          <div>
            <h1>PLANET CONTROL</h1>
            <p className="mono small">FIELD HAZARD OPS · FL / GA / AL</p>
          </div>
        </div>
        <div className="hud-stats mono small">
          <span className={online ? "ok" : "warn"}>● {online ? "LIVE" : "OFFLINE"}</span>
          <span>{sites.length} SITES</span>
          <span className="stop">{counts.stop} STOP</span>
          <span className="warn">{counts.warn} WARN</span>
          <span className="hide-sm">⚡ {lightning.length}/30m</span>
          <span className="hide-sm">UTC {now.toISOString().slice(11, 19)}</span>
        </div>
        <div className="hud-controls">
          <div className="seg" role="group" aria-label="Sensor mode">
            {SENSORS.map(([k, label]) => (
              <button key={k} className={sensor === k ? "on" : ""} onClick={() => setSensor(k)}>{label}</button>
            ))}
          </div>
          <button onClick={() => globe.current?.flyHome()} title="North America" aria-label="Show North America">🌎</button>
          <button onClick={() => globe.current?.flySites()} title="All sites" aria-label="Show all sites">◎<span className="hide-sm"> Sites</span></button>
          <button onClick={() => { setFocus(null); globeRef.current?.flyHome(); }}>Change location</button></div>
>>>>>>> Stashed changes
      </header>

      <main className="layout">
        <section className="map-wrap" aria-label="Map of Alabama, Georgia and Florida">
          <div
            className="map"
            style={{ aspectRatio: `${MAP_SIZE.width} / ${MAP_SIZE.height}` }}
          >
            <img src="/map.png" alt="Map of Alabama, Georgia and Florida" />
            {cities.map((c) => (
              <button
                key={c.name}
                className={`pin ${c.name === selectedName ? "pin--active" : ""}`}
                style={{
                  left: `${(c.x / MAP_SIZE.width) * 100}%`,
                  top: `${(c.y / MAP_SIZE.height) * 100}%`,
                  background: hazardColor(c.hazard),
                }}
                onClick={() => {
                  setSelectedName(c.name);
                  setRankOpen(false);
                }}
                aria-label={`${c.name}, hazard index ${c.hazard} of 10`}
                title={c.name}
              />
            ))}
            {onMap && (
              <div
                className="me-marker"
                style={{
                  left: `${(myPixel.x / MAP_SIZE.width) * 100}%`,
                  top: `${(myPixel.y / MAP_SIZE.height) * 100}%`,
                }}
                title="You are here!"
              />
            )}
          </div>
        </section>

        <aside className="panel">
          <div className="info">
            {selected ? (
              <>
                <div className="city-head">
                  <h2 className="city">{selected.name}</h2>
                  <span className="city-time">
                    {now.toLocaleTimeString("en-US", {
                      timeZone: selected.tz,
                      timeZoneName: "short",
                    })}
                  </span>
                </div>
                {selected.info.map((line) => (
                  <p key={line} className="info-line">
                    {line}
                  </p>
                ))}

                <div className="reports">
                <h3 className="reports-title">Crew reports ({siteReports.length})</h3>
                <br></br>
                {siteReports.length === 0 && (
                  <p className="hint">No reports for this site yet.</p>
                )}
                {siteReports.map((r) => (
                  <article key={r.id} className="report">
                    <p className="report-meta">
                      <strong>{r.type}</strong> · {new Date(r.time).toLocaleString()}
                    </p>
                    <p className="report-text">{r.text}</p>
                  </article>
                ))}  
              </div>
              </>
            ) : (
              <>
                <p className="info-line">General Info 1</p>
                <p className="info-line">General Info 2</p>
                <p className="info-line">General Info 3</p>
                <p className="hint">Select a city on the map to see its details.</p>
              </>
            )}
          </div>

          <div className="supervisor">
            <button
              className="rank-btn"
              disabled={!selected}
              onClick={() => setRankOpen((o) => !o)}
            >
              SUPERVISOR RANK BUTTON
            </button>

            {rankOpen && selected && (
              <div className="rank-picker" role="group" aria-label="Set hazard rank">
                <p>Set hazard rank for {selected.name}</p>
                <div className="rank-options">
                  {Array.from({ length: 11 }, (_, n) => (
                    <button
                      key={n}
                      className={n === selected.hazard ? "on" : ""}
                      onClick={() => setHazard(n)}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <br></br>
            <button
              className="rank-btn crew-btn"
              disabled={!selected}
              onClick={() => {
                setReportOpen((o) => !o);
                setRankOpen(false);
              }}
            >
              CREW REPORT BUTTON
            </button>

<<<<<<< Updated upstream
            {reportOpen && selected && (
              <form className="report-form" onSubmit={submitReport}>
                <p>New report for {selected.name}</p>
                <select value={reportType} onChange={(e) => setReportType(e.target.value)}>
                  <option>Wildlife</option>
                  <option>Weather</option>
                  <option>Accident</option>
                  <option>Equipment</option>
                  <option>Other</option>
                </select>
                <textarea
                  value={reportText}
                  onChange={(e) => setReportText(e.target.value)}
                  placeholder="Describe what you're seeing, e.g. alligator near the east gate"
                  rows={4}
                  required
                />
                <button type="submit">Submit report</button>
              </form>
            )}
          </div>
        </aside>
      </main>
=======
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast toast--${t.kind}`}>{t.text}</div>)}
      </div>

      {!focus && (
          <div className="landing-overlay">
            <Landing sites={sites} onDone={handleLandingDone} />
          </div>
        )}
>>>>>>> Stashed changes
    </div>
  );
}

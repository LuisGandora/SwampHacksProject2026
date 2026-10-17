import { useState, useEffect } from "react";
import { CITIES, MAP_SIZE, latLonToPixel } from "./data.js";

// Green (safe) -> Yellow-ish (in the middle) ->  red (big hazard) for a 0-10 index.
const hazardColor = (v) => `hsl(${120 - v * 12}, 70%, 42%)`;

export default function App() {
  const [me, setMe] = useState(null);
  const [locError, setLocError] = useState("");
  const [now, setNow] = useState(new Date());

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

  return (
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
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { API, api, setTokenGetter } from "./api.js";
import { useAuth } from "./auth.js";
import { flush, queued } from "./offlineQueue.js";
import AlertsPanel from "./components/AlertsPanel.jsx";
import ComplianceModal from "./components/ComplianceModal.jsx";
import ModelModal from "./components/ModelModal.jsx";
import NotifyModal from "./components/NotifyModal.jsx";
import StopWorkBanner from "./components/StopWorkBanner.jsx";
import MapLayers from "./components/MapLayers.jsx";
import CrewReportForm from "./components/CrewReportForm.jsx";
import GlobeMap from "./components/GlobeMap.jsx";
import RankingPanel from "./components/RankingPanel.jsx";
import ReportModal from "./components/ReportModal.jsx";
import SiteCard from "./components/SiteCard.jsx";
import { categoryLabel } from "./hazard.js";
import Landing from "./components/landing.jsx";
import { sitesWithin } from "./geo.js";

const SENSORS = [["normal", "STD"], ["nvg", "NVG"], ["flir", "FLIR"], ["crt", "CRT"]];
const POLL = { sites: 60000, alerts: 20000, alertsDuringStop: 8000, lightning: 60000 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function usePoll(fn, ms, deps = []) {
  useEffect(() => {
    let live = true;
    const tick = () => live && fn();
    tick();
    const id = setInterval(tick, ms);
    return () => { live = false; clearInterval(id); };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
}

export default function App() {
  const auth = useAuth();
  const [now, setNow] = useState(new Date());
  const [health, setHealth] = useState(null);
  const [apiError, setApiError] = useState("");
  const [categories, setCategories] = useState([]);
  const [horizon, setHorizon] = useState("next_day");
  const [sites, setSites] = useState([]);
  const [loadingSites, setLoadingSites] = useState(true);
  const [alerts, setAlerts] = useState([]);
  const [lightning, setLightning] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [crewOpen, setCrewOpen] = useState(false);
  const [pinMode, setPinMode] = useState(false);
  const [pin, setPin] = useState(null);
  const [me, setMe] = useState(null);
  const [sensor, setSensor] = useState("normal");
  const [aiSite, setAiSite] = useState(null);
  const [complianceSite, setComplianceSite] = useState(null);
  const [modelOpen, setModelOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [tab, setTab] = useState("rank");
  const [notifyOn, setNotifyOn] = useState(() => "Notification" in window && Notification.permission === "granted");
  const [refreshKey, setRefreshKey] = useState(0);
  const [notifyOpen, setNotifyOpen] = useState(false);
  const [showPins, setShowPins] = useState(false);
  const [radar, setRadar] = useState(false);
  const [warnOn, setWarnOn] = useState(false);
  const [warnings, setWarnings] = useState(null);
  const [layersOpen, setLayersOpen] = useState(false);
  const [pins, setPins] = useState([]);
  const [queuedCount, setQueuedCount] = useState(0);
  const [demo, setDemo] = useState(null);  // { step, total, text, site_id, done }
  const demoRun = useRef(0);
  const seenAlerts = useRef(null);
  const globe = useRef(null);
  const [focus, setFocus] = useState(null); // null = landing page still showing

  const toast = useCallback((text, kind = "info") => {
    const id = Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "stop" ? 12000 : 5000);
  }, []);

  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  useEffect(() => { setTokenGetter(auth.enabled && auth.isAuthenticated ? auth.getToken : null); },
            [auth.enabled, auth.isAuthenticated]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api.health().then((h) => { setHealth(h); setApiError(""); }).catch((e) => setApiError(e.message));
    api.categories().then(setCategories).catch(() => {});
  }, []);
  useEffect(() => {
    if (!navigator.geolocation) return;
    const w = navigator.geolocation.watchPosition((p) => setMe({ lat: p.coords.latitude, lon: p.coords.longitude }),
                                                  () => {}, { enableHighAccuracy: true, maximumAge: 60000 });
    return () => navigator.geolocation.clearWatch(w);
  }, []);

  const loadSites = () => api.sites(horizon)
    .then((s) => { setSites(s); setApiError(""); })
    .catch((e) => setApiError(e.message))
    .finally(() => setLoadingSites(false));
  usePoll(loadSites, POLL.sites, [horizon, refreshKey]);

  const loadAlerts = () => api.alerts().then((list) => {
    setAlerts(list);
    const ids = new Set(list.map((a) => a.alert_id));
    if (seenAlerts.current) {  // announce stop-work alerts that are new since the last poll
      list.filter((a) => a.level === "stop_work" && !seenAlerts.current.has(a.alert_id)).forEach((a) => {
        toast(`⛔ STOP WORK at ${a.site_name}: ${a.message || categoryLabel(a.category, categories)}`, "stop");
        if (notifyOn) new Notification(`STOP WORK: ${a.site_name}`, { body: a.message || a.title, tag: String(a.alert_id) });
        navigator.vibrate?.([200, 100, 200]);
      });
    }
    seenAlerts.current = ids;
  }).catch(() => {});
  const anyStop = alerts.some((a) => a.level === "stop_work");
  usePoll(loadAlerts, anyStop ? POLL.alertsDuringStop : POLL.alerts, [notifyOn, refreshKey, anyStop]);
  useEffect(() => { if (showPins) api.hazardPins().then(setPins).catch((e) => toast(e.message, "error")); },
            [showPins, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  usePoll(() => warnOn && api.nwsAlertMap().then(setWarnings).catch((e) => toast(e.message, "error")),
          120000, [warnOn, refreshKey]);
  const setLayer = (key, on) => ({ radar: setRadar, warnings: setWarnOn, pins: setShowPins })[key](on);

  // Reports saved on the phone without signal go out as soon as it's back online (and every minute while waiting).
  const sendQueued = useCallback(async () => {
    if (!navigator.onLine) return;
    const r = await flush();
    setQueuedCount(r.left);
    if (r.sent) { toast(`${r.sent} saved report${r.sent > 1 ? "s" : ""} sent.`); setRefreshKey((k) => k + 1); }
    if (r.rejected) toast(`${r.rejected} saved report${r.rejected > 1 ? "s were" : " was"} rejected by the server.`, "error");
  }, [toast]);
  useEffect(() => {
    queued().then((q) => { setQueuedCount(q.length); if (q.length && auth.isAuthenticated) sendQueued(); });
    window.addEventListener("online", sendQueued);
    return () => window.removeEventListener("online", sendQueued);
  }, [sendQueued, auth.isAuthenticated]);
  usePoll(() => queuedCount && auth.isAuthenticated && sendQueued(), 60000, [queuedCount, auth.isAuthenticated]);
  usePoll(() => api.lightning().then(setLightning).catch(() => {}), POLL.lightning, [refreshKey]);

  const selected = sites.find((s) => s.site_id === selectedId) || null;
  const select = useCallback((id) => {
    setSelectedId(id);
    setTab("site");
    const s = sites.find((x) => x.site_id === id);
    if (s) globe.current?.flyTo(s.lon, s.lat, 9);
  }, [sites]);

  const refreshAll = () => setRefreshKey((k) => k + 1);
  const toggleNotify = async () => {
    if (notifyOn) return setNotifyOn(false);
    const p = await Notification.requestPermission();
    setNotifyOn(p === "granted");
    if (p !== "granted") toast("Desktop alerts are blocked in this browser's settings.");
  };
  const dev = (fn, msg) => async () => {
    try { await fn(selectedId); toast(msg); refreshAll(); } catch (e) { toast(e.message, "error"); }
  };

  // One-click demo of the brief's story on the selected (or top Florida) site, narrated step by step.
  const runDemo = async () => {
    const site = selected || sites.find((s) => s.state === "FL") || sites[0];
    if (!site) return;
    const run = ++demoRun.current;
    const step = async (n, text, ms, action) => {
      if (demoRun.current !== run) throw new Error("stopped");
      setDemo({ step: n, total: 6, text, site_id: site.site_id });
      const out = action ? await action() : null;
      refreshAll();
      await sleep(ms);
      return out;
    };
    try {
      await step(1, `6:00 AM. A crew heads out to ${site.name} (${site.county}, ${site.state}).`, 4000,
                 async () => select(site.site_id));
      const s = await step(2, "Heat index 108°F, a week of rain has softened the ground, and a crew member reports an " +
                              "alligator by the jobsite, with a photo.", 7000, () => api.startScenario(site.site_id));
      await step(3, "Lightning 4 miles away: STOP WORK within 10 miles. The 30-minute all-clear countdown starts.", 7000);
      await step(4, "Another strike, 3 miles out: the countdown restarts at 30:00. Nobody goes back out early.", 8000,
                 () => api.fakeStrike(site.site_id));
      await step(5, "The supervisor's ranking puts this site on top (live floor 8/10), and each user's Discord or " +
                    "Slack channel gets the alert.", 7000, async () => setTab("rank"));
      await step(6, `6 PM: tomorrow's briefing goes out. "${s?.summary || "Tomorrow: heat and afternoon storms."}"`, 9000);
      if (demoRun.current === run) setDemo((d) => d && { ...d, done: true, text: "That's the brief's day. Reset clears the demo data." });
    } catch (e) {
      if (e.message !== "stopped") { toast(e.message, "error"); setDemo(null); }
    }
  };
  const resetDemo = async () => {
    const id = demo?.site_id;
    demoRun.current++;
    setDemo(null);
    if (id) { try { await api.stopScenario(id); toast("Demo cleared."); } catch (e) { toast(e.message, "error"); } }
    refreshAll();
  };

  const counts = { stop: alerts.filter((a) => a.level === "stop_work").length, warn: alerts.filter((a) => a.level === "warning").length };
  const online = health && !apiError;

  // The landing page lets the user focus on an area (GPS or a dropped pin + radius). Until they choose, focus is
  // null and the Landing overlay is shown; after that the globe and ranking only show the in-range sites.
  const visibleSites = sitesWithin(sites, focus);
  const handleLandingDone = (f) => {
    setFocus(f);
    if (f.all) {
      globe.current?.flyHome();
    } else {
      setMe({ lat: f.lat, lon: f.lon });
      globe.current?.flyRadius(f.lon, f.lat, f.radius);
    }
  };

  return (
    <div className="app">
      <GlobeMap ref={globe} sites={visibleSites} lightning={lightning} selectedId={selectedId} onSelect={select}
                pinMode={pinMode} pin={pin} onPin={(p) => { setPin(p); setPinMode(false); setTab("site"); }}
                pins={showPins ? pins : null} warnings={warnOn ? warnings : null} radar={radar} me={me} sensor={sensor} />
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
          <span>{visibleSites.length} SITES</span>
          <span className="stop">{counts.stop} STOP</span>
          <span className="warn">{counts.warn} WARN</span>
          <span className="hide-sm">⚡ {lightning.length}/30m</span>
          <span className="hide-sm">UTC {now.toISOString().slice(11, 19)}</span>
          {queuedCount > 0 && (
            <button className="link small warn" onClick={sendQueued} title="Saved on this device; sent when back online">
              ⏳ {queuedCount} QUEUED
            </button>
          )}
        </div>
        <div className="hud-controls">
          <div className="seg" role="group" aria-label="Sensor mode">
            {SENSORS.map(([k, label]) => (
              <button key={k} className={sensor === k ? "on" : ""} onClick={() => setSensor(k)}>{label}</button>
            ))}
          </div>
          <button className={radar || warnOn || showPins ? "on" : ""} onClick={() => setLayersOpen((v) => !v)}
                  aria-pressed={layersOpen} title="Map layers (radar, NWS warnings, hazard pins) and legend">
            🗺<span className="hide-sm"> Layers</span></button>
          {health?.dev && (
            <button onClick={demo ? resetDemo : runDemo} title="Play the brief's story">{demo ? "■ Reset" : "▶ Demo"}</button>
          )}
          {auth.enabled && !auth.isAuthenticated ? (
            <button className="primary" onClick={auth.login} disabled={auth.isLoading}>Sign in</button>
          ) : (
            <>
              <button onClick={() => setNotifyOpen(true)} title="Discord / Slack notifications" aria-label="Notifications">🔔</button>
              {auth.enabled && (
                <button onClick={auth.logout} title={`Signed in as ${auth.user?.name || auth.user?.email}. Sign out`}
                        aria-label="Sign out" className="user-btn">
                  {auth.user?.picture ? <img src={auth.user.picture} alt="" /> : "⎋"}
                </button>
              )}
            </>
          )}
          <button onClick={() => setModelOpen(true)} title="Model accuracy (backtest)" aria-label="Model accuracy">📈<span className="hide-sm"> Model</span></button>
          <button onClick={() => globe.current?.flyHome()} title="North America" aria-label="Show North America">🌎</button>
          <button onClick={() => globe.current?.flySites()} title="All sites" aria-label="Show all sites">◎<span className="hide-sm"> Sites</span></button>
          <button onClick={() => { setFocus(null); globe.current?.flyHome(); }} title="Pick a different area">Change location</button>
        </div>
      </header>

      {apiError && (
        <div className="banner" role="alert">
          {apiError} Start it with <code>python -m uvicorn backend.app:app</code>, or set <code>VITE_API_URL</code> (now {API}).
        </div>
      )}
      <StopWorkBanner alerts={alerts} selectedId={selectedId} now={now} categories={categories} onSelect={select} />
      {layersOpen && (
        <MapLayers layers={{ radar, warnings: warnOn, pins: showPins }} setLayer={setLayer} pinCount={pins.length}
                   warnings={warnings} onClose={() => setLayersOpen(false)} />
      )}
      {demo && (
        <div className="demo-caption" role="status">
          <span className="mono small">{demo.done ? "DEMO" : `STEP ${demo.step}/${demo.total}`}</span>
          <p>{demo.text}</p>
          <button onClick={resetDemo}>■ Reset demo</button>
        </div>
      )}
      {pinMode && <div className="banner banner--pin">Tap the map where the hazard is · <button className="link" onClick={() => setPinMode(false)}>Cancel</button></div>}

      <nav className="tabs" aria-label="Panels">
        {[["rank", "Ranking"], ["site", "Site"], ["alerts", `Alerts${counts.stop ? ` (${counts.stop})` : ""}`]].map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </nav>

      <div className={`dock dock--left tab-${tab === "rank" ? "show" : "hide"}`}>
        <RankingPanel sites={visibleSites} horizon={horizon} setHorizon={setHorizon} selectedId={selectedId}
                      onSelect={select} categories={categories} loading={loadingSites} />
      </div>

      <div className="dock dock--right">
        <div className={`tab-${tab === "site" ? "show" : "hide"}`}>
          {crewOpen ? (
            <section className="panel">
              <CrewReportForm site={selected} categories={categories} pin={pin} pinMode={pinMode} setPinMode={setPinMode}
                              me={me} onCancel={() => { setCrewOpen(false); setPinMode(false); setPin(null); }}
                              signedIn={auth.isAuthenticated} onSignIn={auth.login}
                              onDone={(r) => {
                                setCrewOpen(false); setPin(null);
                                if (r.queued) {
                                  setQueuedCount((n) => n + 1);
                                  toast("No signal: report saved on this phone. It sends automatically when you're back online.");
                                } else toast(`Report sent${r.site_id ? "; site alerted" : ""}. Thanks for keeping the crew safe.`);
                                refreshAll();
                              }} />
            </section>
          ) : selected ? (
            <SiteCard summary={selected} horizon={horizon} categories={categories} alerts={alerts} now={now}
                      dev={health?.dev} refreshKey={refreshKey} onClose={() => setSelectedId(null)}
                      onReport={() => setCrewOpen(true)} onAiReport={() => setAiSite(selected)}
                      onCompliance={() => setComplianceSite(selected)} onChanged={refreshAll}
                      onScenario={dev(api.startScenario, "Demo scenario running on this site.")}
                      onStrike={dev(api.fakeStrike, "Strike recorded 3 mi away: all-clear countdown restarted.")}
                      onClearScenario={dev(api.stopScenario, "Demo cleared.")} />
          ) : (
            <section className="panel empty">
              <h2>Select a site</h2>
              <p className="muted">Tap a dot on the globe or a row in the ranking. Dots are colored by hazard index:
                Low, Moderate, High, Critical. Red rings pulse at sites under a stop-work alert; yellow flashes are
                lightning in the last 30 minutes.</p>
              <button onClick={() => setCrewOpen(true)}>📍 Report a hazard anywhere</button>
            </section>
          )}
        </div>
        <div className={`tab-${tab === "alerts" ? "show" : "hide"}`}>
          <AlertsPanel alerts={alerts} categories={categories} now={now} onSelect={select}
                       notifyOn={notifyOn} onToggleNotify={toggleNotify} />
        </div>
      </div>

      {aiSite && <ReportModal site={aiSite} categories={categories} onClose={() => setAiSite(null)} />}
      {complianceSite && <ComplianceModal site={complianceSite} categories={categories} onClose={() => setComplianceSite(null)} />}
      {modelOpen && <ModelModal onClose={() => setModelOpen(false)} />}
      {notifyOpen && <NotifyModal sites={sites} selected={selected} onClose={() => setNotifyOpen(false)} />}

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast toast--${t.kind}`}>{t.text}</div>)}
      </div>

      {!focus && (
        <div className="landing-overlay">
          <Landing sites={sites} onDone={handleLandingDone} />
        </div>
      )}
    </div>
  );
}

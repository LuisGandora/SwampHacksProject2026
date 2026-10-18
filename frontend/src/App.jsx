import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, ClipboardList, Compass, FlaskConical, Globe, Layers, LineChart, LocateFixed, Play, Square } from "lucide-react";
import { api, setActiveRole, setCacheUser, setTokenGetter } from "./api.js";
import { useAuth } from "./auth.js";
import { flush, queued } from "./offlineQueue.js";
import AlertsPanel from "./components/AlertsPanel.jsx";
import ComplianceModal from "./components/ComplianceModal.jsx";
import ModelModal from "./components/ModelModal.jsx";
import NotifyModal from "./components/NotifyModal.jsx";
import StopWorkBanner from "./components/StopWorkBanner.jsx";
import MapLayers from "./components/MapLayers.jsx";
import AccountMenu from "./components/AccountMenu.jsx";
import PlanModal from "./components/PlanModal.jsx";
import BriefingScreen from "./components/BriefingScreen.jsx";
import CrewReportForm from "./components/CrewReportForm.jsx";
import GlobeMap from "./components/GlobeMap.jsx";
import RankingPanel from "./components/RankingPanel.jsx";
import ReportModal from "./components/ReportModal.jsx";
import SiteCard from "./components/SiteCard.jsx";
import NearbyAlerts from "./components/NearbyAlerts.jsx";
import { categoryLabel } from "./hazard.js";
import Landing from "./components/landing.jsx";
import { sitesWithin } from "./geo.js";

const SENSORS = [["normal", "STD"], ["nvg", "NVG"], ["flir", "FLIR"], ["crt", "CRT"]];
const POLL = { sites: 60000, alerts: 20000, alertsDuringStop: 8000, lightning: 60000 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function usePoll(fn, ms, deps = []) {
  useEffect(() => {
    let live = true;
    let pending = false;
    const tick = async () => {
      if (!live || pending) return;
      pending = true;
      try { await fn(); } finally { pending = false; }
    };
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
  const [feedStatus, setFeedStatus] = useState(null);
  const [dataErrors, setDataErrors] = useState({});
  const [lastAlertAt, setLastAlertAt] = useState(null);
  const dataError = (name, message = null) => setDataErrors((previous) => ({ ...previous, [name]: message }));
  const [categories, setCategories] = useState([]);
  const [horizon, setHorizon] = useState("now");
  const [sites, setSites] = useState([]);
  const [loadingSites, setLoadingSites] = useState(true);
  const [alerts, setAlerts] = useState([]);
  const [lightning, setLightning] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [inspectedLocation, setInspectedLocation] = useState(null);
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
  const [basemap, setBasemapState] = useState(() => {   // "dark" (CARTO) or "satellite" (Esri), remembered here
    try { return localStorage.getItem("planit.basemap") || "dark"; } catch { return "dark"; }
  });
  const setBasemap = (b) => { setBasemapState(b); try { localStorage.setItem("planit.basemap", b); } catch { /* private mode */ } };
  const [warnOn, setWarnOn] = useState(false);
  const [warnings, setWarnings] = useState(null);
  const [layersOpen, setLayersOpen] = useState(false);
  const [pins, setPins] = useState([]);
  const [queuedCount, setQueuedCount] = useState(0);
  const [demo, setDemo] = useState(null);  // { step, total, text, site_id, done }
  const demoRun = useRef(0);
  const seenAlerts = useRef(null);
  const inView = useRef(() => true);                         // is an alert's site in the area you picked?
  const globe = useRef(null);
  const [focus, setFocus] = useState(null); // null = landing page still showing
  const [landingPlace, setLandingPlace] = useState(null); // starting point picked on the landing page
  const [reopened, setReopened] = useState(false);          // the 🧭 Location button skips the welcome step
  const [accountOpen, setAccountOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [saved, setSaved] = useState([]);                    // your saved locations (signed in)
  const starBusy = useRef(false);                            // one save/unsave at a time (double clicks)
  const [identity, setIdentityState] = useState(null);       // "crew" | "supervisor", remembered per account
  const [briefing, setBriefing] = useState(null);            // { kind: "start" | "6pm" } while the briefing screen is open
  const shown6pm = useRef("");
  const hud = useRef(null);                                  // the header: banners sit just below its real height
  useEffect(() => {
    const el = hud.current;
    if (!el || !window.ResizeObserver) return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--hud-bottom", `${el.offsetTop + el.offsetHeight}px`));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);                               // the day the supervisor's 6 PM screen last popped up

  const toast = useCallback((text, kind = "info") => {
    const id = Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "stop" ? 12000 : 5000);
  }, []);

  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  useEffect(() => { setCacheUser(auth.user?.sub); }, [auth.user?.sub]);
  useEffect(() => { setTokenGetter(auth.enabled && auth.isAuthenticated ? auth.getToken : null); },
            [auth.enabled, auth.isAuthenticated]); // eslint-disable-line react-hooks/exhaustive-deps
  const identityKey = `planit.identity.${auth.user?.sub || "local"}`;
  useEffect(() => {  // before anything loads per-identity data: restore the identity this account used last
    if (!auth.isAuthenticated) return;
    let v = null;
    try { v = localStorage.getItem(identityKey); } catch { /* private mode */ }
    setActiveRole(v);
    setIdentityState(v);
  }, [auth.isAuthenticated, identityKey]);
  const setIdentity = (v) => {
    try { localStorage.setItem(identityKey, v); } catch { /* private mode */ }
    setActiveRole(v);
    setIdentityState(v);
    setSaved([]);
    setRefreshKey((k) => k + 1);
  };
  const loadSaved = () => auth.isAuthenticated && api.locations().then(setSaved).catch(() => {});
  useEffect(() => { loadSaved(); }, [auth.isAuthenticated, focus, identity]); // eslint-disable-line react-hooks/exhaustive-deps
  const savedSite = (id) => {
    const s = sites.find((x) => x.site_id === id);
    return saved.find((l) => l.site_id === id || (s && Math.abs(l.lat - s.lat) < 5e-4 && Math.abs(l.lon - s.lon) < 5e-4));
  };
  useEffect(() => {
    api.categories().then(setCategories).catch(() => {});
  }, []);
  usePoll(() => api.health().then((h) => { setHealth(h); dataError("API"); })
    .catch((e) => dataError("API", e.message)), 30000, []);
  usePoll(() => api.dataStatus().then((s) => { setFeedStatus(s); dataError("Feed status"); })
    .catch(() => dataError("Feed status", "Feed freshness could not be verified.")), 30000, []);
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
    setLastAlertAt(Date.now());
    dataError("Alerts");
    const ids = new Set(list.map((a) => a.alert_id));
    if (seenAlerts.current) {  // announce stop-work alerts that are new since the last poll
      list.filter((a) => a.level === "stop_work" && !seenAlerts.current.has(a.alert_id) && inView.current(a)).forEach((a) => {
        toast(`⛔ STOP WORK at ${a.site_name}: ${a.message || categoryLabel(a.category, categories)}`, "stop");
        if (notifyOn) new Notification(`STOP WORK: ${a.site_name}`, { body: a.message || a.title, tag: String(a.alert_id) });
        navigator.vibrate?.([200, 100, 200]);
      });
    }
    seenAlerts.current = ids;
  }).catch(() => dataError("Alerts", "Live alerts could not be refreshed; showing the last received alerts."));
  const anyStop = alerts.some((a) => a.level === "stop_work");
  usePoll(loadAlerts, anyStop ? POLL.alertsDuringStop : POLL.alerts, [notifyOn, refreshKey, anyStop]);
  usePoll(() => showPins && api.hazardPins().then((p) => { setPins(p); dataError("Hazard pins"); })
    .catch(() => dataError("Hazard pins", "Hazard pins could not be refreshed.")), 15000, [showPins, refreshKey]);
  usePoll(() => warnOn && api.nwsAlertMap().then((w) => { setWarnings(w); dataError("NWS map"); })
    .catch(() => dataError("NWS map", "NWS map warnings could not be refreshed.")),
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
  usePoll(() => api.lightning().then((l) => { setLightning(l); dataError("Lightning map"); })
    .catch(() => dataError("Lightning map", "Lightning map could not be refreshed.")), POLL.lightning, [refreshKey]);

  const selected = sites.find((s) => s.site_id === selectedId) || null;
  const select = useCallback((id) => {
    setSelectedId(id);
    setInspectedLocation(null);
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

  // Your briefing: pops up for supervisors at 6 PM local, once a day, and replays (Account → Replay 6 PM briefing)
  // until the next 6 PM. "Run again" inside rebuilds it. 🧪 Briefing (demo mode only) opens it any time for debugging.
  const canBrief = auth.isAuthenticated || (!auth.enabled && !!health?.dev);
  const sixKey = `planit.brief6pm.${auth.user?.sub || "local"}`;
  const due6pm = () => {
    const today = new Date().toDateString();
    if (identity !== "supervisor" || new Date().getHours() < 18 || shown6pm.current === today) return false;
    try { if (localStorage.getItem(sixKey) === today) { shown6pm.current = today; return false; } localStorage.setItem(sixKey, today); }
    catch { /* private mode: the ref still keeps it to once per session */ }
    shown6pm.current = today;
    return true;
  };
  useEffect(() => { if (canBrief && focus && due6pm()) setBriefing({ kind: "6pm" }); },
            [now.getMinutes(), identity, focus, canBrief]); // eslint-disable-line react-hooks/exhaustive-deps

  const visibleSites = sitesWithin(sites, focus);
  const inViewIds = new Set(visibleSites.map((s) => s.site_id));
  const viewAlerts = focus && !focus.all ? alerts.filter((a) => inViewIds.has(a.site_id)) : alerts;
  inView.current = (a) => !focus || focus.all || inViewIds.has(a.site_id);
  const counts = { stop: viewAlerts.filter((a) => a.level === "stop_work").length, warn: viewAlerts.filter((a) => a.level === "warning").length };
  const feedProblems = (feedStatus?.feeds || []).filter((f) => f.status !== "current");
  const activeErrors = Object.entries(dataErrors).filter(([name, message]) => message
    && (name !== "Hazard pins" || showPins) && (name !== "NWS map" || warnOn));
  const alertsReliable = lastAlertAt && now.getTime() - lastAlertAt < 45000 && !dataErrors.Alerts
    && feedStatus && now - new Date(feedStatus.checked_at) < 90000
    && !dataErrors["Feed status"] && feedStatus?.feeds?.find((f) => f.name === "lightning")?.status === "current"
    && feedStatus?.feeds?.find((f) => f.name === "alerts")?.status === "current";
  const online = health && !apiError && !activeErrors.length && !feedProblems.length && alertsReliable;
  const oldStatus = feedStatus && now - new Date(feedStatus.checked_at) > 90000;

  // The landing page lets the user focus on an area (GPS or a dropped pin + radius). Until they choose, focus is
  // null and the Landing overlay is shown; after that the globe and ranking only show the in-range sites.
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
    <div className={`app ${focus ? "" : "landing-mode"}`}>
      <GlobeMap ref={globe} sites={focus ? visibleSites : []} lightning={lightning} selectedId={selectedId} onSelect={select}
                inspectedLocation={focus ? inspectedLocation : null}
                onLocation={(location) => {
                  if (!focus || crewOpen) return;
                  setSelectedId(null); setInspectedLocation(location); setTab("site");
                }}
                pinMode={!focus || pinMode} pin={focus ? pin : landingPlace}
                onPin={(p) => focus ? (setPin(p), setPinMode(false), setTab("site")) : setLandingPlace({ ...p, label: "Dropped pin" })}
                pins={showPins ? pins : null} warnings={warnOn ? warnings : null} radar={radar} me={me} sensor={sensor} basemap={basemap} />
      {sensor === "crt" && <div className="scanlines" aria-hidden="true" />}

      <header className="hud" ref={hud}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">◎</span>
          <div>
            <h1>PLANET CONTROL</h1>
            <p className="mono small">FIELD HAZARD OPS · FL / GA / AL</p>
          </div>
        </div>
        <div className="hud-stats mono small">
          <span className={online ? "ok" : "warn"}>● {online ? "LIVE" : "DATA UNVERIFIED"}</span>
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
            <Layers className="ico" aria-hidden="true" /><span className="hide-sm"> Layers</span></button>
          {health?.dev && (
            <button onClick={demo ? resetDemo : runDemo} title="Play the brief's story">{demo ? <><Square className="ico" aria-hidden="true" /> Reset</> : <><Play className="ico" aria-hidden="true" /> Demo</>}</button>
          )}
          {auth.enabled && !auth.isAuthenticated ? (
            <button className="primary" onClick={auth.login} disabled={auth.isLoading}>Sign in</button>
          ) : (
            <>
              <button onClick={() => setNotifyOpen(true)} title="Discord / Slack notifications" aria-label="Notifications"><Bell className="ico" aria-hidden="true" /></button>
              {auth.enabled && (
                <button onClick={() => setAccountOpen((v) => !v)}
                        title={`Signed in as ${auth.user?.name || auth.user?.email}, acting as ${identity || "crew"}`}
                        aria-label="Your account" aria-expanded={accountOpen} className="user-btn">
                  {auth.user?.picture ? <img src={auth.user.picture} alt="" /> : "⎋"}
                </button>
              )}
              {auth.enabled && (
                <span className={`identity-chip ${identity === "supervisor" ? "sup" : ""}`}>{identity === "supervisor" ? "Supervisor" : "Crew"}</span>
              )}
              {health?.dev && canBrief && focus && (  // debugging only: hidden unless PLANIT_DEV=1 (never on the public site)
                <button className="small" onClick={() => setBriefing({ kind: now.getHours() >= 18 ? "6pm" : "start" })}
                        title="Debug: open the briefing now (demo mode only)"><FlaskConical className="ico" aria-hidden="true" /><span className="hide-sm"> Briefing</span></button>
              )}
            </>
          )}
          <button className="primary" onClick={() => setPlanOpen(true)} title="Where to focus attention and resources first"><ClipboardList className="ico" aria-hidden="true" /><span className="hide-sm"> Plan</span></button>
          <button onClick={() => setModelOpen(true)} title="Model accuracy (backtest)" aria-label="Model accuracy"><LineChart className="ico" aria-hidden="true" /><span className="hide-sm"> Model</span></button>
          <button onClick={() => globe.current?.flyHome()} title="North America" aria-label="Show North America"><Globe className="ico" aria-hidden="true" /></button>
          <button onClick={() => globe.current?.flySites()} title="All sites" aria-label="Show all sites"><LocateFixed className="ico" aria-hidden="true" /><span className="hide-sm"> Sites</span></button>
          <button onClick={() => { setReopened(true); setFocus(null); globe.current?.flyHome(); }}
                  title="Change your position, or show all sites" aria-label="Change location"><Compass className="ico" aria-hidden="true" /><span className="hide-sm"> Location</span></button>
        </div>
      </header>

      <div className="data-notices">
      {apiError && (
        <div className="banner" role="alert">
          Site rankings could not be refreshed. Previously received scores may be outdated.
        </div>
      )}
      {(activeErrors.length > 0 || feedProblems.length > 0 || oldStatus) && (
        <details className="banner data-warning">
          <summary>Some live data is unavailable or incomplete · View details</summary>
          <ul>
            {activeErrors.map(([name, message]) => <li key={name}>{name}: {message}</li>)}
            {feedProblems.map((f) => <li key={f.name}>{f.name}: {f.status}
              {f.last_success && ` · last success ${new Date(f.last_success).toLocaleString()}`}
              {f.detail && <span className="small"> · {f.detail}</span>}</li>)}
            {oldStatus && <li>Feed status is out of date.</li>}
          </ul>
        </details>
      )}
      </div>
      <StopWorkBanner alerts={viewAlerts} selectedId={selectedId} now={now} categories={categories} onSelect={select} reliable={alertsReliable} />
      {layersOpen && (
        <MapLayers layers={{ radar, warnings: warnOn, pins: showPins }} setLayer={setLayer} pinCount={pins.length}
                   basemap={basemap} setBasemap={setBasemap}
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
                      onSelect={select} categories={categories} loading={loadingSites} now={now} />
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
                      alertsReliable={alertsReliable} lastAlertAt={lastAlertAt} onSelectNearby={select}
                      dev={health?.dev} refreshKey={refreshKey} onClose={() => setSelectedId(null)}
                      onReport={() => setCrewOpen(true)} onAiReport={() => setAiSite(selected)}
                      onCompliance={() => setComplianceSite(selected)} onChanged={refreshAll}
                      saved={!!savedSite(selected.site_id)}
                      onToggleSave={async () => {
                        if (!auth.isAuthenticated) return toast("Sign in to save locations.");
                        if (starBusy.current) return;
                        starBusy.current = true;
                        const s = savedSite(selected.site_id);
                        try {
                          if (s) await api.deleteLocation(s.location_id).catch((e) => { if (e.status !== 404) throw e; });  // already gone
                          else await api.saveLocation({ name: selected.name.slice(0, 80), lat: selected.lat, lon: selected.lon,
                                                        radius_mi: 25, site_id: selected.site_id });
                          toast(s ? `Removed ${selected.name} from saved locations.`
                                  : `Saved ${selected.name}: it gets a 6 PM briefing for plants within 25 mi.`);
                        } catch (e) { toast(e.message, "error"); }
                        await loadSaved();
                        starBusy.current = false;
                      }}
                      onScenario={dev(api.startScenario, "Demo scenario running on this site.")}
                      onStrike={dev(api.fakeStrike, "Strike recorded 3 mi away: all-clear countdown restarted.")}
                      onClearScenario={dev(api.stopScenario, "Demo cleared.")} />
          ) : inspectedLocation ? (
            <section className="panel">
              <header className="panel-head">
                <div><h2>Selected location</h2>
                  <p className="small muted">{inspectedLocation.lat.toFixed(5)}, {inspectedLocation.lon.toFixed(5)}</p></div>
                <button className="icon-btn" onClick={() => setInspectedLocation(null)} aria-label="Close location">✕</button>
              </header>
              <NearbyAlerts location={inspectedLocation} alerts={alerts} categories={categories} now={now}
                            reliable={alertsReliable} lastUpdated={lastAlertAt} onSelect={select} />
            </section>
          ) : (
            <section className="panel empty">
              <h2>Select a location</h2>
              <p className="muted">Click anywhere on the map to check live alerts within 10 miles, or select a site for its details.
                Dots are colored by hazard index:
                Low, Moderate, High, Critical. Red rings pulse at sites under a stop-work alert; yellow flashes are
                lightning in the last 30 minutes.</p>
              <button onClick={() => setCrewOpen(true)}>📍 Report a hazard anywhere</button>
            </section>
          )}
        </div>
        <div className={`tab-${tab === "alerts" ? "show" : "hide"}`}>
          <AlertsPanel alerts={viewAlerts} categories={categories} now={now} onSelect={select}
                       scope={focus && !focus.all ? `Sites in your area (${visibleSites.length})` : "All sites"}
                       notifyOn={notifyOn} onToggleNotify={toggleNotify} reliable={alertsReliable} lastUpdated={lastAlertAt} />
        </div>
      </div>

      {aiSite && <ReportModal site={aiSite} categories={categories} onClose={() => setAiSite(null)} />}
      {complianceSite && <ComplianceModal site={complianceSite} categories={categories} onClose={() => setComplianceSite(null)} />}
      {modelOpen && <ModelModal onClose={() => setModelOpen(false)} />}
      {planOpen && <PlanModal sites={visibleSites} categories={categories} onSelect={select} onClose={() => setPlanOpen(false)}
                             identity={identity || "crew"} />}
      {briefing && focus && (
        <BriefingScreen key={`${identity}:${JSON.stringify(focus)}`} focus={focus} kind={briefing.kind} identity={identity || "crew"}
                        onSelect={select} onClose={() => setBriefing(null)} toast={toast}
                        onNotify={() => { setBriefing(null); setNotifyOpen(true); }} />
      )}
      {accountOpen && auth.enabled && auth.isAuthenticated && (
        <AccountMenu auth={auth} categories={categories} saved={saved} onSavedChange={loadSaved}
                     identity={identity || "crew"} onIdentity={setIdentity}
                     onBriefing={focus ? () => { setAccountOpen(false); setBriefing({ kind: "6pm" }); } : null}
                     onClose={() => setAccountOpen(false)}
                     onGoTo={(l) => { setAccountOpen(false); handleLandingDone({ lat: l.lat, lon: l.lon, radius: Number(l.radius_mi), label: l.name }); }} />
      )}
      {notifyOpen && <NotifyModal sites={sites} selected={selected} onClose={() => setNotifyOpen(false)} />}

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast toast--${t.kind}`}>{t.text}</div>)}
      </div>

      {!focus && (
        <div className="landing-overlay">
          <Landing sites={sites} auth={auth} place={landingPlace} setPlace={setLandingPlace} reopened={reopened}
                   identity={identity} onIdentity={setIdentity}
                   flyTo={(lon, lat) => globe.current?.flyTo(lon, lat, 8)} onDone={handleLandingDone} />
        </div>
      )}
    </div>
  );
}

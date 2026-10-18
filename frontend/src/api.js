// Planet Control API client. Set VITE_API_URL in frontend/.env to point at a tunnel or deployment.
export const API = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

// The signed-in user's Auth0 access token is attached to every call (set by App once signed in).
let tokenGetter = null;
export const setTokenGetter = (fn) => { tokenGetter = fn; };
// The identity the user is acting as (crew or supervisor): each has its own saved locations, channels and reports.
let activeRole = "crew";
export const setActiveRole = (role) => { activeRole = role || "crew"; };

// Who's signed in, so this browser's saved AI answers are kept per account and identity.
let cacheUser = "local";
export const setCacheUser = (sub) => { cacheUser = sub || "local"; };

// The last AI answer (briefing, safety report, compliance) is saved in this browser. The server replays the same
// answer until the next 6 PM; this copy is only used when the server can't be reached (offline=true).
async function remember(name, call) {
  const key = `planit.ai.${cacheUser}.${activeRole}.${name}`;
  try {
    const data = await call();
    try { localStorage.setItem(key, JSON.stringify(data)); }
    catch { Object.keys(localStorage).filter((k) => k.startsWith("planit.ai.")).forEach((k) => localStorage.removeItem(k)); }
    return data;
  } catch (e) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(key)); } catch { /* private mode */ }
    if (e.status === 0 && saved) return { ...saved, offline: true };
    throw e;
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", json, form, timeout = 30000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const headers = { ...(json ? { "Content-Type": "application/json" } : {}), "X-Planit-Role": activeRole };
    if (tokenGetter) {
      try { headers.Authorization = `Bearer ${await tokenGetter()}`; } catch { /* signed out or offline: send without */ }
    }
    const res = await fetch(API + path, {
      method,
      signal: ctrl.signal,
      headers,
      body: json ? JSON.stringify(json) : form,
    });
    const data = (res.headers.get("content-type") || "").includes("json") ? await res.json() : await res.text();
    if (!res.ok) {
      const detail = typeof data === "object" ? data.detail : data;
      throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e.name === "AbortError") throw new ApiError(0, "The request timed out.");
    throw new ApiError(0, `Can't reach the Planet Control API at ${API}.`);
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  health: () => request("/health"),
  dataStatus: () => request("/data-status"),
  categories: () => request("/hazard-categories"),
  sites: (horizon) => request(`/sites?horizon=${horizon}&limit=1000`),
  site: (id) => request(`/sites/${id}`),
  forecast: (id) => request(`/sites/${id}/forecast?hours=48`),
  weather: (id, refresh = false) => request(`/sites/${id}/weather${refresh ? "?refresh=true" : ""}`, { timeout: 90000 }),
  alerts: () => request("/alerts/active"),
  lightning: () => request("/lightning/recent?minutes=30"),
  briefing: (id) => request(`/briefings/${id}`),
  report: (id, refresh = false) => remember(`report.${id}`, () =>
    request(`/sites/${id}/report${refresh ? "?refresh=true" : ""}`, { method: "POST", timeout: 120000 })),
  economics: (id) => request(`/sites/${id}/economics?weeks=8`),
  siteContracts: (id) => request(`/sites/${id}/contracts?limit=50`),
  compliance: (id, refresh = false) => remember(`compliance.${id}`, () =>
    request(`/sites/${id}/compliance${refresh ? "?refresh=true" : ""}`, { method: "POST", timeout: 120000 })),
  setCrewReportStatus: (reportId, status) => request(`/crew-reports/${reportId}`, { method: "PATCH", json: { status } }),
  deleteCrewReport: (reportId) => request(`/crew-reports/${reportId}`, { method: "DELETE" }),
  modelBacktest: () => request("/model/backtest"),
  setAssumption: (key, value) => request(`/economics/assumptions/${key}`, { method: "PUT", json: { value } }),
  plan: (h, ids) => request(`/plan/${h}?limit=50${ids ? `&site_ids=${ids.join(",")}` : ""}`, { timeout: 90000 }),
  crewReports: (id) => request(`/crew-reports?site_id=${id}&status=open&limit=20`),
  upload: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/uploads", { method: "POST", form, timeout: 90000 });
  },
  createCrewReport: (report) => request("/crew-reports", { method: "POST", json: report }),
  hazardPins: () => request("/hazard-pins?days=1095"),
  nwsAlertMap: () => request("/nws-alerts/map"),
  me: () => request("/me"),
  channels: () => request("/me/channels"),
  locations: () => request("/me/locations"),
  myReports: () => request("/me/reports?limit=50"),
  saveLocation: (l) => request("/me/locations", { method: "POST", json: l }),
  deleteLocation: (id) => request(`/me/locations/${id}`, { method: "DELETE" }),
  locationBriefing: (id) => request(`/me/locations/${id}/briefing`, { method: "POST", timeout: 60000 }),
  addChannel: (c) => request("/me/channels", { method: "POST", json: c }),
  deleteChannel: (id) => request(`/me/channels/${id}`, { method: "DELETE" }),
  sendToChannel: (id, text) => request(`/me/channels/${id}/send`, { method: "POST", json: { text } }),
  myBriefing: (b) => remember(`briefing.${b.kind}`, () => request("/me/briefing", { method: "POST", json: b, timeout: 150000 })),
  testChannel: (id) => request(`/me/channels/${id}/test`, { method: "POST" }),
  startScenario: (id) => request(`/dev/scenario/${id}`, { method: "POST", timeout: 90000 }),
  stopScenario: (id) => request(`/dev/scenario/${id}`, { method: "DELETE" }),
  fakeStrike: (id) => request(`/dev/lightning/${id}?miles_away=3`, { method: "POST" }),
};

// Planet Control API client. Set VITE_API_URL in frontend/.env to point at a tunnel or deployment.
export const API = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

// The signed-in user's Auth0 access token is attached to every call (set by App once signed in).
let tokenGetter = null;
export const setTokenGetter = (fn) => { tokenGetter = fn; };

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
    const headers = json ? { "Content-Type": "application/json" } : {};
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
  categories: () => request("/hazard-categories"),
  sites: (horizon) => request(`/sites?horizon=${horizon}&limit=1000`),
  site: (id) => request(`/sites/${id}`),
  forecast: (id) => request(`/sites/${id}/forecast?hours=48`),
  weather: (id, refresh = false) => request(`/sites/${id}/weather${refresh ? "?refresh=true" : ""}`, { timeout: 90000 }),
  alerts: () => request("/alerts/active"),
  lightning: () => request("/lightning/recent?minutes=30"),
  briefing: (id) => request(`/briefings/${id}`),
  report: (id, refresh = false) =>
    request(`/sites/${id}/report${refresh ? "?refresh=true" : ""}`, { method: "POST", timeout: 120000 }),
  economics: (id) => request(`/sites/${id}/economics?weeks=8`),
  siteContracts: (id) => request(`/sites/${id}/contracts?limit=50`),
  compliance: (id, refresh = false) =>
    request(`/sites/${id}/compliance${refresh ? "?refresh=true" : ""}`, { method: "POST", timeout: 120000 }),
  setCrewReportStatus: (reportId, status) => request(`/crew-reports/${reportId}`, { method: "PATCH", json: { status } }),
  modelBacktest: () => request("/model/backtest"),
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
  addChannel: (c) => request("/me/channels", { method: "POST", json: c }),
  deleteChannel: (id) => request(`/me/channels/${id}`, { method: "DELETE" }),
  testChannel: (id) => request(`/me/channels/${id}/test`, { method: "POST" }),
  startScenario: (id) => request(`/dev/scenario/${id}`, { method: "POST", timeout: 90000 }),
  stopScenario: (id) => request(`/dev/scenario/${id}`, { method: "DELETE" }),
  fakeStrike: (id) => request(`/dev/lightning/${id}?miles_away=3`, { method: "POST" }),
};

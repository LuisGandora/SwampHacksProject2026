// PlanIT Control API client. Set VITE_API_URL in frontend/.env to point at a tunnel or deployment.
export const API = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

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
    const res = await fetch(API + path, {
      method,
      signal: ctrl.signal,
      headers: json ? { "Content-Type": "application/json" } : undefined,
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
    throw new ApiError(0, `Can't reach the PlanIT API at ${API}.`);
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
  alerts: () => request("/alerts/active"),
  lightning: () => request("/lightning/recent?minutes=30"),
  briefing: (id) => request(`/briefings/${id}`),
  report: (id, refresh = false) =>
    request(`/sites/${id}/report${refresh ? "?refresh=true" : ""}`, { method: "POST", timeout: 120000 }),
  economics: (id) => request(`/sites/${id}/economics?weeks=8`),
  crewReports: (id) => request(`/crew-reports?site_id=${id}&status=open&limit=20`),
  upload: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/uploads", { method: "POST", form, timeout: 90000 });
  },
  createCrewReport: (report) => request("/crew-reports", { method: "POST", json: report }),
  startScenario: (id) => request(`/dev/scenario/${id}`, { method: "POST", timeout: 90000 }),
  stopScenario: (id) => request(`/dev/scenario/${id}`, { method: "DELETE" }),
  fakeStrike: (id) => request(`/dev/lightning/${id}?miles_away=3`, { method: "POST" }),
};

// Crew reports made without signal wait in IndexedDB (photo included) and are sent when the phone is back online.
// Each report carries its own report_id, so a retry after a half-finished send never creates a duplicate.
import { api } from "./api.js";

const STORE = "reports";

function db() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("planet-control", 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: "report.report_id" });
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function run(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const t = d.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req.result);
    t.onerror = () => reject(t.error);
  });
}

export const queued = () => run("readonly", (s) => s.getAll()).catch(() => []);
export const enqueue = (report, photo) => run("readwrite", (s) => s.put({ report, photo }));
const drop = (id) => run("readwrite", (s) => s.delete(id));
const save = (item) => run("readwrite", (s) => s.put(item));

export const newId = () => crypto.randomUUID?.() ??  // randomUUID needs HTTPS; plain-http LAN testing falls back
  "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
    (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16));

// Send what's waiting. Stops at the first network error (still offline); drops reports the API rejects as invalid.
export async function flush() {
  let sent = 0, rejected = 0;
  for (const item of await queued()) {
    try {
      if (item.photo && !item.report.photo_url) {
        item.report.photo_url = (await api.upload(item.photo)).url;
        await save(item); // don't upload the photo twice if the report itself fails next
      }
      await api.createCrewReport(item.report);
      await drop(item.report.report_id);
      sent++;
    } catch (e) {
      if (e.status === 0 || e.status === 401 || e.status === 429 || e.status >= 500) break; // try again later
      await drop(item.report.report_id);
      rejected++;
    }
  }
  return { sent, rejected, left: (await queued()).length };
}

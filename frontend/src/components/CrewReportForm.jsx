import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { enqueue, newId } from "../offlineQueue.js";

const CREW_FIRST = ["wildlife", "slip_trip", "ppe", "heavy_machinery", "osha_inspection", "flooding",
                    "soil_saturation", "extreme_heat", "lightning", "high_wind", "severe_storm"];
const touch = () => window.matchMedia?.("(pointer: coarse)").matches;
const NAME_KEY = "planit.reporter";

// Downscale to <= 1600 px JPEG on the device: phone photos are 3-12 MB, the API limit is 10 MB.
async function shrink(file, max = 1600) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
    if (scale === 1 && file.type === "image/jpeg" && file.size < 2_500_000) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    return new File([blob], `${file.name.replace(/\.\w+$/, "") || "photo"}.jpg`, { type: "image/jpeg" });
  } catch {
    return file; // e.g. HEIC on a browser that can't decode it: send as-is (the API accepts HEIC)
  }
}

export default function CrewReportForm({ site, categories, pin, pinMode, setPinMode, me, onDone, onCancel, signedIn, onSignIn }) {
  const [category, setCategory] = useState("wildlife");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState(3);
  const [name, setName] = useState(() => { try { return localStorage.getItem(NAME_KEY) || ""; } catch { return ""; } });
  const [where, setWhere] = useState("site");
  const [photo, setPhoto] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const camera = useRef(null), library = useRef(null);

  useEffect(() => () => preview && URL.revokeObjectURL(preview), [preview]);
  useEffect(() => { if (where !== "pin") setPinMode(false); }, [where]);

  const options = [...categories].sort((a, b) =>
    (CREW_FIRST.indexOf(a.category) + 1 || 99) - (CREW_FIRST.indexOf(b.category) + 1 || 99));
  const location = where === "gps" ? me : where === "pin" ? pin : site && { lat: site.lat, lon: site.lon };

  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/") && !/\.(heic|heif)$/i.test(file.name)) return setError("Please choose an image.");
    setError("");
    setBusy("Preparing photo…");
    const small = await shrink(file);
    setBusy("");
    setPhoto(small);
    setPreview(URL.createObjectURL(small));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!location) return setError(where === "pin" ? "Tap the map to drop the pin." : "Location isn't available yet.");
    setError("");
    try { localStorage.setItem(NAME_KEY, name); } catch { /* private mode: fine */ }
    const report = {
      report_id: newId(), reported_at: new Date().toISOString(),
      category, description: description.trim() || null, severity, photo_url: null, reported_by: name.trim() || null,
      lat: location.lat, lon: location.lon, site_id: where === "site" ? site?.site_id : null,
    };
    const queue = async () => { await enqueue(report, photo); onDone({ ...report, queued: true }); };
    try {
      if (!navigator.onLine) return await queue();
      if (photo) {
        setBusy("Uploading photo…");
        report.photo_url = (await api.upload(photo)).url;
      }
      setBusy("Sending report…");
      onDone(await api.createCrewReport(report));
    } catch (err) {
      // No signal (or it dropped mid-send): keep the report on the phone and send it when back online.
      if (err.status === 0) {
        try { await queue(); } catch { setError(`${err.message} Couldn't save it on this device either.`); }
      } else setError(err.message);
    } finally {
      setBusy("");
    }
  };

  if (!signedIn) return (
    <div className="crew-form">
      <h3>Report a hazard</h3>
      <p className="muted small">Sign in so your report is tied to you and your team gets notified.</p>
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel}>Cancel</button>
        <button type="button" className="primary" onClick={onSignIn}>Sign in</button>
      </div>
    </div>
  );

  return (
    <form className="crew-form" onSubmit={submit} aria-label="Report a hazard">
      <h3>Report a hazard{site ? ` · ${site.name}` : ""}</h3>

      <label>Hazard
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {options.map((c) => <option key={c.category} value={c.category}>{c.label}</option>)}
        </select>
      </label>

      <label>What do you see?
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
                  placeholder="e.g. Alligator in the swamp next to the east gate" maxLength={1000} />
      </label>

      <fieldset className="severity">
        <legend>Severity</legend>
        <div className="seg">
          {[1, 2, 3, 4, 5].map((n) => (
            <button type="button" key={n} className={severity === n ? "on" : ""} onClick={() => setSeverity(n)}
                    aria-pressed={severity === n}>{n}</button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>Location</legend>
        <div className="seg">
          <button type="button" className={where === "site" ? "on" : ""} onClick={() => setWhere("site")} disabled={!site}>Site</button>
          <button type="button" className={where === "gps" ? "on" : ""} onClick={() => setWhere("gps")} disabled={!me}>My GPS</button>
          <button type="button" className={where === "pin" ? "on" : ""}
                  onClick={() => { setWhere("pin"); setPinMode(true); }}>Pin on map</button>
        </div>
        <p className="muted small">
          {where === "pin" ? (pinMode ? "Tap the map to drop the pin." : pin ? `Pinned at ${pin.lat.toFixed(4)}, ${pin.lon.toFixed(4)}` : "")
            : location ? `${location.lat.toFixed(4)}, ${location.lon.toFixed(4)}` : "Waiting for location…"}
        </p>
      </fieldset>

      <fieldset>
        <legend>Photo (optional)</legend>
        <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={pick} />
        <input ref={library} type="file" accept="image/*,.heic,.heif" hidden onChange={pick} />
        {preview ? (
          <div className="photo-preview">
            <img src={preview} alt="Selected hazard photo" />
            <button type="button" className="link" onClick={() => { setPhoto(null); setPreview(null); }}>Remove</button>
          </div>
        ) : (
          <div className="photo-buttons">
            {touch() && <button type="button" onClick={() => camera.current.click()}>📷 Take photo</button>}
            <button type="button" onClick={() => library.current.click()}>
              🖼 {touch() ? "Choose from library" : "Attach photo"}
            </button>
          </div>
        )}
      </fieldset>

      <label>Your name (optional)
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoComplete="name" />
      </label>

      {error && <p className="error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={!!busy}>Cancel</button>
        <button type="submit" className="primary" disabled={!!busy}>{busy || "Send report"}</button>
      </div>
    </form>
  );
}

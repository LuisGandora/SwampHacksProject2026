const KEY = import.meta.env.VITE_GEMINI_API_KEY;
const MODEL = import.meta.env.VITE_GEMINI_MODEL || "gemini-2.5-flash";

const PROMPT = (sites) => `You write a short next-day safety briefing for field crews at power plant sites.
Below is JSON for the user's nearby and pinned sites (score is a 0-10 hazard index, higher is worse;
null means no score yet). Reply with ONLY JSON in this shape:
{"summary": "2-4 sentences covering overall safety, the worst site, the best site, and anything to watch",
 "tips": ["up to 3 short action items"]}
Use only the data given. Do not invent weather or incidents.

Sites:
${JSON.stringify(sites)}`;

export async function fetchBriefing(sites, signal) {
  if (!KEY) throw new Error("no key");
  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`,
    {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json", "x-goog-api-key": KEY },
      body: JSON.stringify({
        contents: [{ parts: [{ text: PROMPT(sites) }] }],
        generationConfig: { responseMimeType: "application/json" },
      }),
    }
  );
  if (!res.ok) throw new Error(`Gemini ${res.status}`);
  const data = await res.json();
  return JSON.parse(data.candidates[0].content.parts[0].text);
}

export function localSummary(rows) {
  const scored = rows.filter((r) => r.score != null);
  if (!rows.length) return null;
  if (!scored.length) return { summary: `${rows.length} sites in view, but none have a hazard score yet.`, tips: [] };
  const worst = scored[0], best = scored[scored.length - 1];
  const stops = rows.filter((r) => r.stop_work_alerts > 0);
  const avg = scored.reduce((a, r) => a + r.score, 0) / scored.length;
  return {
    summary:
      `${rows.length} sites in view, average hazard ${avg.toFixed(1)}/10. ` +
      `Highest risk is ${worst.name} (${worst.score.toFixed(1)}), lowest is ${best.name} (${best.score.toFixed(1)}).` +
      (stops.length ? ` ${stops.length} site${stops.length > 1 ? "s have" : " has"} an active stop-work alert.` : ""),
    tips: stops.map((s) => `Hold work at ${s.name} until the stop-work alert clears`),
  };
}
"""Deliver new alerts and 6 PM briefings to a chat webhook (Discord or Slack).

    NOTIFY_WEBHOOK_URL   Discord or Slack incoming-webhook URL (unset = nothing is sent or marked delivered)
    NOTIFY_LEVELS        alert levels to send, default "stop_work" (use "stop_work,warning" for everything)

Each alert and briefing is sent once (notified_at). Only alerts that started in the last 30 minutes are sent,
so turning the webhook on later doesn't flood the channel with old ones.
"""
import os

import httpx

client = httpx.Client(timeout=30)
MAX_CHARS = 1900  # Discord rejects messages over 2,000 characters

NEW_ALERTS = """
SELECT a.alert_id, a.level, a.title, a.message, a.image_url, a.site_name, s.state,
       ST_Y(s.geog::geometry) AS lat, ST_X(s.geog::geometry) AS lon, a.all_clear_at
  FROM active_site_alerts a JOIN sites s USING (site_id)
 WHERE a.notified_at IS NULL AND a.level = ANY (%s) AND a.started_at > now() - interval '30 minutes'
 ORDER BY a.level DESC, a.started_at
"""


def chunks(lines, limit=MAX_CHARS):
    """Pack lines into messages under the webhook's size limit."""
    out, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line[:limit]
    return out + ([cur] if cur else [])


def post(url, text):
    body = {"content": text} if "discord" in url else {"text": text}
    client.post(url, json=body).raise_for_status()


def alert_line(a):
    clear = f", all-clear {a['all_clear_at']:%H:%M} UTC" if a["all_clear_at"] else ""
    line = (f"{'🛑' if a['level'] == 'stop_work' else '⚠️'} **{a['title']}** at {a['site_name']} ({a['state']}): "
            f"{a['message'] or ''}{clear} · https://maps.google.com/?q={a['lat']:.4f},{a['lon']:.4f}")
    return line + (f" {a['image_url']}" if a["image_url"] else "")


def sync(conn):
    url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not url:
        return "skipped (set NOTIFY_WEBHOOK_URL)"
    levels = os.environ.get("NOTIFY_LEVELS", "stop_work").split(",")
    alerts = conn.execute(NEW_ALERTS, (levels,)).fetchall()
    briefs = conn.execute("SELECT b.site_id, b.briefing_date, b.summary FROM daily_briefings b "
                          "WHERE b.notified_at IS NULL AND b.sent_at > now() - interval '1 hour'").fetchall()
    for text in chunks([alert_line(a) for a in alerts] +
                       [f"📋 **Briefing for {b['briefing_date']:%a %b %d}**: {b['summary']}" for b in briefs]):
        post(url, text)
    conn.execute("UPDATE site_alerts SET notified_at = now() WHERE alert_id = ANY (%s)", ([a["alert_id"] for a in alerts],))
    conn.execute("UPDATE daily_briefings SET notified_at = now() WHERE (site_id, briefing_date) IN "
                 "(SELECT * FROM unnest(%s::bigint[], %s::date[]))",
                 ([b["site_id"] for b in briefs], [b["briefing_date"] for b in briefs]))
    return f"sent {len(alerts)} alerts and {len(briefs)} briefings"

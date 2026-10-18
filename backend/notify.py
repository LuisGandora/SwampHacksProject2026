"""Deliver alerts, crew reports and 6 PM briefings to each user's own Discord or Slack channels.

Users add channels in the app (POST /me/channels): a webhook URL, which alert levels, which sites, and whether to
include briefings and crew reports. Webhook URLs are secrets (anyone holding one can post to the channel), so:
  - only https://discord.com/api/webhooks/... and https://hooks.slack.com/services/... are accepted, which also
    keeps the worker from being pointed at internal addresses;
  - they're stored encrypted with WEBHOOK_KEY (a Fernet key in .env, never in the database);
  - the API returns only the last 4 characters, and errors are logged without the URL.

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # -> WEBHOOK_KEY

Each item goes to each channel once (notification_log). Only items from the last 30 minutes (briefings: 1 hour)
are sent, so a new channel doesn't get flooded with old ones.
"""
import os

import httpx
from cryptography.fernet import Fernet, InvalidToken

client = httpx.Client(timeout=30)
MAX_CHARS = 1900  # Discord rejects messages over 2,000 characters
WEBHOOK_PREFIXES = {"discord": ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/"),
                    "slack": ("https://hooks.slack.com/services/",)}

ALERTS = """
SELECT a.alert_id, a.site_id, a.level, a.title, a.message, a.image_url, a.site_name, s.state,
       ST_Y(s.geog::geometry) AS lat, ST_X(s.geog::geometry) AS lon, a.all_clear_at
  FROM active_site_alerts a JOIN sites s USING (site_id)
 WHERE a.started_at > now() - interval '30 minutes' AND a.message NOT LIKE '%%Crew report:%%'  -- sent as reports
 ORDER BY a.level DESC, a.started_at
"""
BRIEFINGS = """
SELECT b.site_id, b.briefing_date, b.summary, s.name AS site_name FROM daily_briefings b JOIN sites s USING (site_id)
 WHERE b.sent_at > now() - interval '1 hour'
"""
REPORTS = """
SELECT r.report_id, r.site_id, r.category, r.description, r.photo_url, r.severity, r.reported_by,
       s.name AS site_name, ST_Y(r.geog::geometry) AS lat, ST_X(r.geog::geometry) AS lon
  FROM crew_reports r LEFT JOIN sites s USING (site_id)
 WHERE r.received_at > now() - interval '30 minutes' AND r.status = 'open'
"""


def webhook_kind(url):
    """'discord' or 'slack' for an allowed webhook URL, else None."""
    if not isinstance(url, str) or len(url) > 500 or any(c.isspace() for c in url):
        return None
    return next((k for k, prefixes in WEBHOOK_PREFIXES.items() if url.startswith(prefixes)), None)


def fernet():
    key = os.environ.get("WEBHOOK_KEY")
    if not key:
        raise RuntimeError("Set WEBHOOK_KEY in .env (see backend/notify.py) to store notification channels.")
    return Fernet(key)


def encrypt(url):
    return fernet().encrypt(url.encode()).decode()


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
    # Slack's markup uses *bold*, Discord's **bold**
    body = ({"content": text, "allowed_mentions": {"parse": []}} if webhook_kind(url) == "discord"  # no mass pings
            else {"text": text.replace("**", "*")})
    client.post(url, json=body).raise_for_status()


def safe_error(e):
    """Error text without the webhook URL (httpx puts the URL in its messages)."""
    return f"HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else type(e).__name__


def maps(lat, lon):
    return f"https://maps.google.com/?q={lat:.4f},{lon:.4f}"


def alert_line(a):
    clear = f", all-clear {a['all_clear_at']:%H:%M} UTC" if a["all_clear_at"] else ""
    line = (f"{'🛑' if a['level'] == 'stop_work' else '⚠️'} **{a['title']}** at {a['site_name']} ({a['state']}): "
            f"{a['message'] or ''}{clear} · {maps(a['lat'], a['lon'])}")
    return line + (f" {a['image_url']}" if a["image_url"] else "")


def report_line(r):
    where = r["site_name"] or "no site nearby"
    sev = f" (severity {r['severity']}/5)" if r["severity"] else ""
    line = (f"📍 **Crew report: {r['category'].replace('_', ' ')}**{sev} at {where}: {r['description'] or ''} "
            f"· by {r['reported_by'] or 'crew'} · {maps(r['lat'], r['lon'])}")
    return line + (f" {r['photo_url']}" if r["photo_url"] else "")


LOCATION_BRIEFINGS = """
SELECT b.location_id, b.briefing_date, b.summary, l.user_id FROM location_briefings b JOIN saved_locations l USING (location_id)
 WHERE b.sent_at > now() - interval '1 hour'
"""


def items(conn):
    """Everything sendable right now: (key, site_id, kind, level, text)."""
    return ([(f"alert:{a['alert_id']}", a["site_id"], "alert", a["level"], alert_line(a))
             for a in conn.execute(ALERTS).fetchall()] +
            [(f"report:{r['report_id']}", r["site_id"], "report", None, report_line(r))
             for r in conn.execute(REPORTS).fetchall()] +
            [(f"briefing:{b['site_id']}:{b['briefing_date']}", b["site_id"], "briefing", None,
              f"📋 **{b['site_name']}, briefing for {b['briefing_date']:%a %b %d}**: {b['summary']}")
             for b in conn.execute(BRIEFINGS).fetchall()] +
            [(f"locbrief:{b['location_id']}:{b['briefing_date']}", None, "location", None,
              f"📋 **Your saved location** · {b['summary']}", b["user_id"])
             for b in conn.execute(LOCATION_BRIEFINGS).fetchall()])


def wants(channel, item):
    _, site_id, kind, level, _, *owner = item
    if kind == "location":  # a saved location's briefing goes only to its owner's briefing channels
        return channel["briefings"] and channel["user_id"] == owner[0]
    if channel["site_ids"] is not None and site_id not in channel["site_ids"]:
        return False
    return {"alert": level in channel["levels"], "report": channel["crew_reports"],
            "briefing": channel["briefings"]}[kind]


def sync(conn):
    channels = conn.execute("SELECT * FROM notification_channels").fetchall()
    if not channels:
        return "no channels yet (users add them under Notifications)"
    todo = items(conn)
    sent = {(r["channel_id"], r["item"]) for r in conn.execute(
        "SELECT channel_id, item FROM notification_log WHERE item = ANY (%s)", ([i[0] for i in todo],))}
    f, total = fernet(), 0
    for ch in channels:
        mine = [i for i in todo if (ch["channel_id"], i[0]) not in sent and wants(ch, i)]
        if not mine:
            continue
        try:
            url = f.decrypt(ch["webhook_encrypted"].encode()).decode()
            for text in chunks([i[4] for i in mine]):
                post(url, text)
        except (httpx.HTTPError, InvalidToken) as e:
            conn.execute("UPDATE notification_channels SET last_error = %s WHERE channel_id = %s",
                         (safe_error(e), ch["channel_id"]))
            continue
        conn.execute("INSERT INTO notification_log (channel_id, item) SELECT %s, unnest(%s::text[]) "
                     "ON CONFLICT DO NOTHING", (ch["channel_id"], [i[0] for i in mine]))
        conn.execute("UPDATE notification_channels SET last_sent_at = now(), last_error = NULL WHERE channel_id = %s",
                     (ch["channel_id"],))
        total += len(mine)
    return f"sent {total} items to {len(channels)} channels"


def send_test(conn, channel, text="✅ Planet Control is connected. Alerts for your sites will appear here."):
    """Post a message to one channel right now (a hello by default); returns None or an error string."""
    try:
        post(fernet().decrypt(channel["webhook_encrypted"].encode()).decode(), text)
        return None
    except (httpx.HTTPError, InvalidToken) as e:
        conn.execute("UPDATE notification_channels SET last_error = %s WHERE channel_id = %s",
                     (safe_error(e), channel["channel_id"]))
        return safe_error(e)

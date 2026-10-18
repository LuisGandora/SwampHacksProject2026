// Step-by-step guide for connecting Discord or Slack, shown under the 📖 Tutorial button in Notifications.
const Ext = ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a>;

export default function WebhookGuide() {
  return (
    <div className="guide">
      <p className="small">Planet Control can post stop-work alerts, warnings, crew reports and the 6 PM briefing to your
        own Discord or Slack channel. You need to be signed in, and allowed to add a webhook in that server or workspace
        (or ask its admin).</p>

      <details>
        <summary>1. Create the link in Discord</summary>
        <ol>
          <li>Open your server and hover over the channel for alerts. Click ⚙ <strong>Edit Channel</strong>.</li>
          <li>Go to <strong>Integrations → Webhooks → New Webhook</strong>.</li>
          <li>Optional: name it "Planet Control".</li>
          <li>Click <strong>Copy Webhook URL</strong>. It starts with <code>https://discord.com/api/webhooks/</code>.</li>
        </ol>
        <p className="small">More help: <Ext href="https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks">Discord: Intro to Webhooks</Ext></p>
      </details>

      <details>
        <summary>1. Create the link in Slack</summary>
        <ol>
          <li>Go to <Ext href="https://api.slack.com/apps">api.slack.com/apps</Ext> → <strong>Create New App → From scratch</strong>.</li>
          <li>Name it "Planet Control", pick your workspace, and click <strong>Create App</strong>.</li>
          <li>Open <strong>Incoming Webhooks</strong> in the left menu and turn <strong>Activate Incoming Webhooks</strong> on.</li>
          <li>Click <strong>Add New Webhook to Workspace</strong>, choose the channel, then <strong>Allow</strong>.</li>
          <li>Copy the <strong>Webhook URL</strong>. It starts with <code>https://hooks.slack.com/services/</code>.</li>
        </ol>
        <p className="small">If Slack says the app needs approval, ask a workspace admin. More help:{" "}
          <Ext href="https://api.slack.com/messaging/webhooks">Slack: Sending messages using incoming webhooks</Ext></p>
      </details>

      <details>
        <summary>2. Connect it here</summary>
        <ol>
          <li>Paste the link into <strong>Webhook URL</strong> below. Optional: give it a name.</li>
          <li>Under <strong>Send</strong>, pick: <strong>Stop work</strong> (lightning within 10 mi, tornado warnings),
            <strong> Warnings</strong> (heat, wind, flood risk, wildlife), <strong>Crew reports</strong> (with photos),
            <strong> 6 PM briefings</strong> (tomorrow's outlook).</li>
          <li>Under <strong>For</strong>, pick <strong>All sites</strong> or <strong>Chosen sites</strong>.</li>
          <li>Click <strong>Add channel</strong>, then <strong>Send test</strong>. "✅ Planet Control is connected…" should
            appear in your channel.</li>
        </ol>
      </details>

      <details>
        <summary>Good to know</summary>
        <ul>
          <li>Each alert is sent once per channel, within about a minute.</li>
          <li>A new channel only gets new alerts (nothing older than 30 minutes).</li>
          <li>You can add up to 10 channels. To change one, delete it and add it again.</li>
          <li><strong>Keep the link private:</strong> anyone with it can post to your channel. It's stored encrypted and
            only the last 4 characters are shown. If it leaks, delete the webhook in Discord or Slack and make a new one.</li>
        </ul>
      </details>

      <details>
        <summary>If it doesn't work</summary>
        <ul>
          <li><strong>"Paste a Discord … or Slack … webhook URL":</strong> the link isn't a webhook link. Copy it again;
            it must start with <code>https://discord.com/api/webhooks/</code> or <code>https://hooks.slack.com/services/</code>.</li>
          <li><strong>Send test says HTTP 404 or 403:</strong> the webhook was deleted or the Slack app was removed.
            Make a new one and re-add it.</li>
          <li><strong>The test works but real alerts never arrive:</strong> alerts are sent by Planet Control's background
            service. Ask your admin to check it's running.</li>
        </ul>
      </details>
    </div>
  );
}

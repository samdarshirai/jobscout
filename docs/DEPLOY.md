# Deploy — connect Telegram, run continuously

`jobscout serve` (DESIGN §16) runs the Telegram bot, the web app, and a twice-daily Poll (08:00
+ 20:00 local time) in one process. No cloud, no Docker — SQLite on disk means a hard restart
just resumes from the last checkpoint.

**1. Create the bot.** Message [@BotFather](https://t.me/BotFather) on Telegram: `/newbot`,
follow the prompts, copy the token it gives you into `.env` as `TELEGRAM_BOT_TOKEN`.

**2. Get chat ids.** Have both the candidate and the operator send any message to the new bot,
then hit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser — each sender's numeric
`chat.id` is in the JSON. Put them in `.env` as `TELEGRAM_CANDIDATE_CHAT_ID` /
`TELEGRAM_OPERATOR_CHAT_ID`. Every other sender is ignored — this is the only allowlist.

**3. Run it.**

```bash
uv run jobscout serve
```

The web app listens on `127.0.0.1:8000` only — set `JOBSCOUT_WEB_TOKEN` in `.env` and tunnel
in (Tailscale, an SSH port-forward) for access off the machine; it's never exposed directly.

**4. Keep it alive on a Mac** — a `launchd` user agent restarts it if it dies or the machine
reboots:

```xml
<!-- ~/Library/LaunchAgents/com.jobscout.serve.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jobscout.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string><string>uv</string><string>run</string>
    <string>jobscout</string><string>serve</string>
  </array>
  <key>WorkingDirectory</key><string>/absolute/path/to/jobscout</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/absolute/path/to/jobscout/data/serve.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/to/jobscout/data/serve.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.jobscout.serve.plist    # start + survive reboots
launchctl unload ~/Library/LaunchAgents/com.jobscout.serve.plist  # stop
```

## Troubleshooting

- `KeyError: 'TELEGRAM_BOT_TOKEN'` on startup — `.env` isn't loaded or the var's missing; check
  it's actually exported into the shell `jobscout serve` runs in (a `launchd` plist doesn't read
  `.env` on its own — either `EnvironmentVariables` in the plist or load `.env` at process start).
- Bot never responds — confirm the sender's chat id matches `TELEGRAM_CANDIDATE_CHAT_ID` /
  `TELEGRAM_OPERATOR_CHAT_ID` exactly; every other chat id is silently ignored, by design.
- Nothing at `127.0.0.1:8000` — the process crashed before reaching `server.serve()`; check
  `data/serve.log`.

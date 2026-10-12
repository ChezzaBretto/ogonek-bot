# TikTok Ogonyok Bot

A Telegram bot that keeps your TikTok streaks ("ogonyok") alive. Every night it
opens the TikTok web app and sends ❤️ to the chats you picked, so the streak
never resets. It can also auto-reply with a random phrase to videos people send
you.

The bot is personal: it works only for its owner. Other people can't connect
their own TikTok to it.

## How it works

1. You message the bot in Telegram and connect your TikTok account — either by
   scanning a QR code from the app, or by sending a session file (cookies).
2. You tell the bot which chats to send ❤️ to (a list of usernames).
3. From then on the bot runs on its own:
   - at midnight it goes through the list and sends ❤️ to each chat;
   - a few times a day it retries the chats that didn't go through;
   - if auto-reply is on, every few minutes it checks the selected chats and
     replies to new videos with a phrase from your set.

Sending goes through a real browser (Chromium driven by Playwright) — the bot
opens tiktok.com with your session and clicks like a live user. There are random
6–25 second pauses between messages.

You control everything with buttons right in the chat: connect TikTok, show your
chats, set the list, check without sending, send now, configure auto-reply,
disconnect.

## What hardware you need

**One server is enough.** The cheapest Linux VPS will do: 1 core and 1–2 GB of
RAM. It runs everything at once — the bot, the browser, and the schedule (cron).
Chromium is launched strictly one at a time, so even 1 GB is fine.

A home machine or mini-PC that stays on overnight works too — there's no real
difference, you just need Linux and a permanent internet connection.

A note on country: **if you rent the server in your own country, you don't need
anything else — one server is enough.** A second server (a proxy) is only needed
in one case: TikTok ties the session to the country where you logged in, so if
the server is in a different country, TikTok will drop the session. In that case
you route the browser traffic through a SOCKS5 tunnel in the right country (see
below). Telegram still connects directly.

## Install

```bash
git clone https://github.com/ppetrukovich-gif/ogonek-bot.git
cd ogonek-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # if needed, also: playwright install-deps

cp config.example.json config.json
```

Put your bot token from [@BotFather](https://t.me/BotFather) into `config.json`.
You can leave `owner_id` empty — the first person to send `/start` becomes the
owner, and their id is remembered automatically.

Run it:

```bash
python bot.py
```

Send `/start` to the bot and follow the buttons: first "Connect TikTok", then
"Set the list".

## Autostart and schedule

To keep the bot running permanently, set it up as a service (see
`tiktok-ogonek.service.example`):

```bash
sudo cp tiktok-ogonek.service.example /etc/systemd/system/tiktok-ogonek.service
# edit User and the paths in the file, then:
sudo systemctl daemon-reload
sudo systemctl enable --now tiktok-ogonek
```

The nightly send, the retries and the auto-reply run on a schedule via cron
(adjust the paths):

```cron
0  0       * * *  /opt/ogonek-bot/.venv/bin/python /opt/ogonek-bot/daily_run.py
0  7,13,19 * * *  /opt/ogonek-bot/.venv/bin/python /opt/ogonek-bot/retry_run.py
*/10 * * * *      /opt/ogonek-bot/.venv/bin/python /opt/ogonek-bot/autoreply_run.py
```

Times follow the server's timezone — set it with
`timedatectl set-timezone Europe/Moscow` (or your own). The `CRON_TZ` variable
has no effect in cron on Ubuntu, so you have to change the system timezone
itself.

## Session tied to another country

If the server is in one country and your TikTok session was created in another,
route the browser traffic through a SOCKS5 tunnel in the right country. It's set
via an environment variable:

```bash
export TT_PROXY=socks5://127.0.0.1:41080
```

The easiest way to get such a tunnel is over SSH to any machine in the right
country:

```bash
ssh -N -D 127.0.0.1:41080 user@host-in-the-right-country
```

Chromium doesn't support a username/password proxy, so the tunnel must have no
authentication, as in the example above. For cron, put `TT_PROXY` on the first
line of the crontab; for the service, put it in the unit
(`Environment=TT_PROXY=...`).

## Project files

| File | Purpose |
|------|---------|
| `bot.py` | the Telegram bot itself: buttons, commands, dialogs |
| `tt_core.py` | TikTok web handling: session, browser, chats, sending |
| `runner.py` | a single send or check run |
| `daily_run.py` | the nightly send (cron) |
| `retry_run.py` | daytime retries (cron) |
| `autoreply.py`, `autoreply_run.py` | auto-reply to videos (cron) |
| `qr_login.py`, `qr_session.sh` | TikTok login via QR code |
| `notify.py` | service messages to the owner from the cron scripts |

Your data (session, chat lists, settings) is stored in `users/<telegram_id>/`.
That folder and `config.json` are kept out of the repository.

## Security

- `config.json` (the bot token) and the `users/` folder (the TikTok session)
  must never be made public — they're already in `.gitignore`.
- A TikTok session file equals full access to the account. The bot stores it
  with `600` permissions and deletes the message with the file right after
  importing it.
- If the bot token ends up somewhere it shouldn't, revoke it in @BotFather and
  get a new one.

## License

MIT

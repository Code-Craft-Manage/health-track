# Health-Track 💪

A lightweight Telegram bot that runs a **weekly body-measurement check-in** and logs each
session as a row in a Google Sheet. It asks for one measurement at a time, shows a summary for
confirmation, and only responds to a single authorized Telegram user.

- **Stack:** Python, [python-telegram-bot](https://docs.python-telegram-bot.org/) (with JobQueue), [gspread](https://docs.gspread.org/)
- **Auth:** Google OAuth user token (`token.json`)
- **Deploy:** Docker + `docker compose` on a VPS, via GitHub Actions. **All secrets live in GitHub
  Actions secrets — there is no `.env` file on the server.**

Measurements collected, in order (→ 15 sheet columns including the date):

`Date · Weight · Height · Neck · Shoulders · Chest · Biceps L · Biceps R · Waist · Abdomen · Hips · Thigh L · Thigh R · Calf L · Calf R`

---

## 1. Prerequisites

### 1a. Create the Telegram bot (BotFather)

1. In Telegram, open [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a name and a username (must end in `bot`).
3. Copy the **HTTP API token** it gives you → this is `TELEGRAM_BOT_TOKEN`.

### 1b. Find the authorized user's Telegram ID

1. Have each person open [@userinfobot](https://t.me/userinfobot) (or `@RawDataBot`) and press Start.
2. Copy the numeric **`Id`** it replies with. These IDs go into the `USER_TABS` secret (see 4a),
   mapped to each person's spreadsheet tab, e.g. `{"111111111": "Alice", "222222222": "Bob"}`.
   Only IDs listed there may use the bot.
3. Each person must also press **Start** on *your* bot at least once, so the bot is allowed to
   message them (required for the weekly reminder to be delivered).

### 1c. Create the Google Sheet

1. Create a new Google Sheet. Add **one tab per person**, named to match the values in the
   `USER_TABS` secret (see step 1b / 4a). Each authorized user's check-in is logged to their own tab.
2. In **row 1 of each tab**, add these 15 headers, left to right:

   `Date (DD/MM/YYYY)` · `Weight (kg)` · `Height (cm)` · `Neck (cm)` · `Shoulders (cm)` · `Chest (cm)` · `Biceps Left (cm)` · `Biceps Right (cm)` · `Waist (cm)` · `Abdomen (cm)` · `Hips (cm)` · `Thigh Left (cm)` · `Thigh Right (cm)` · `Calf Left (cm)` · `Calf Right (cm)`
3. Copy the **Spreadsheet ID** from the URL → this is `GOOGLE_SHEET_ID`:
   `https://docs.google.com/spreadsheets/d/`**`<THIS_PART>`**`/edit`

### 1d. Enable the Google Sheets API and create an OAuth client

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) → create a project.
2. **APIs & Services → Library →** enable **Google Sheets API**.
3. **APIs & Services → OAuth consent screen →** set it up as **External**.
4. **Publish the app.** On the OAuth consent screen, under *Publishing status*, click **PUBLISH APP**
   so the status reads **In production** — *not* Testing. This is critical: while the app is in
   **Testing**, Google hard-expires the refresh token after **7 days**, which breaks the bot with
   `invalid_grant: Token has been expired or revoked`. (Being a "Test user" does **not** prevent
   this — only leaving Testing mode does.) You don't need Google's verification for a personal
   `spreadsheets`-scope app; you'll just click through a one-time "unverified app" warning in 1e.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app.**
6. **Download** the JSON and save it as **`client_secret.json`** in this project folder.

> We use an OAuth **user token** (not a service account) so the rows are written by your own
> Google account, which already owns the sheet. The sheet does not need to be shared with anyone.

### 1e. Generate `token.json` (one-time, local)

```bash
pip install -r requirements.txt
python generate_token.py
```

A browser opens — log in with the Google account that can edit the sheet. On the
"Google hasn't verified this app" screen, click **Advanced → Go to health-track (unsafe)**;
that's expected for your own app. This writes `token.json`. Then base64-encode it for the
GitHub secret:

```bash
# macOS
base64 -i token.json | pbcopy     # now in your clipboard
# Linux
base64 -w0 token.json
```

That string is `GOOGLE_OAUTH_TOKEN_B64`.

---

## 2. Run locally (optional)

```bash
cp .env.example .env      # fill in the values
# For local dev you can leave GOOGLE_OAUTH_TOKEN_B64 empty and keep token.json
# in this folder instead — the bot falls back to reading the file.
pip install -r requirements.txt
python bot.py
```

Then message your bot `/track` from the authorized account and walk through a check-in.

---

## 3. Project layout

| File | Purpose |
|---|---|
| `bot.py` | Entry point: the `/track` ConversationHandler + the weekly reminder job. |
| `sheets.py` | Google auth + `append_row`; reads back the last logged value per field. Holds the canonical column order (`FIELDS`). |
| `tests/` | `unittest` tests for pure logic (no network/credentials). Run: `python3 -m unittest discover tests`. |
| `config.py` | Loads env vars; resolves the OAuth token from the secret or `token.json`. |
| `generate_token.py` | One-time local OAuth flow → `token.json`. |
| `Dockerfile`, `docker-compose.yml` | Container build + runtime. |
| `.github/workflows/deploy.yml` | Deploy-on-push to the VPS. |

---

## 4. Deploy (GitHub Actions → VPS)

Every push to `main` triggers `.github/workflows/deploy.yml`, which SSHes into the VPS, runs
`git pull`, and rebuilds/restarts the container with `docker compose up -d --build`. Secrets are
forwarded into the deploy shell and substituted by `docker compose` at runtime.

### 4a. GitHub Actions secrets (set these in the repo UI)

**Repo → Settings → Secrets and variables → Actions → New repository secret.** Add all of:

| Secret | Value |
|---|---|
| `SERVER_IP` | VPS hostname / IP |
| `SERVER_USER` | SSH user |
| `SSH_PRIVATE_KEY` | Private key (full PEM) for that user |
| `TELEGRAM_BOT_TOKEN` | from step 1a |
| `USER_TABS` | JSON `{id: tab}` from step 1b — who's allowed in **and** their tab |
| `GOOGLE_SHEET_ID` | from step 1c |
| `GOOGLE_OAUTH_TOKEN_B64` | from step 1e |

> `USER_TABS` is the single source of truth for both the authorized-user allowlist (its keys) and
> per-user tab routing (its values), e.g. `{"111111111": "Alice", "222222222": "Bob"}`. It's a
> secret so real IDs and names stay out of source control.

> SSH port is hard-coded to `2244` in the workflow (matching the other bots). Change it there if
> needed.

### 4b. First-time VPS setup (manual, once)

The workflow assumes the repo is already cloned on the VPS at
`~/htdocs/health-track.codecraftmanage.com` and that the VPS can `git pull`. On the VPS:

```bash
# 1. Docker must be installed (docker + docker compose plugin).
docker --version && docker compose version

# 2. Give the VPS read access to this private repo (deploy key):
ssh-keygen -t ed25519 -C "health-track-vps" -f ~/.ssh/health_track_deploy -N ""
cat ~/.ssh/health_track_deploy.pub
#   → add this as a *Deploy key* in GitHub: repo → Settings → Deploy keys → Add.
#   Then tell git to use it for this repo (e.g. via ~/.ssh/config host alias),
#   or simply clone over HTTPS if the repo is reachable that way.

# 3. Clone into the path the workflow expects:
git clone git@github.com:Code-Craft-Manage/health-track.git ~/htdocs/health-track.codecraftmanage.com
```

After that, every push to `main` deploys automatically. To confirm:

```bash
docker compose -f ~/htdocs/health-track.codecraftmanage.com/docker-compose.yml logs -f healthtrack
```

See [VPS.md](VPS.md) for the quick command reference.

---

## 5. How it behaves

- **Weekly reminder:** every **Saturday at 14:00 (America/Sao_Paulo)** the bot messages the
  authorized user with a *“Start check-in”* button. Tapping it (or typing `/track`) begins the flow.
- **Last-value reminder:** each prompt shows your previous reading for that field and the date it
  was logged (e.g. *“last: 38.5 cm (07/06)”*), read once per check-in from your own tab. It's
  best-effort — a field you've never logged, or a read failure, simply shows no reminder.
- **Validation:** non-numeric input is rejected with a gentle re-prompt; `82,5` (comma) is accepted.
- **Confirmation:** the summary must be confirmed before anything is written. *Cancel* discards it.
- **Access control:** any other Telegram user is refused.

## 6. Refreshing the Google token

As long as the OAuth app is **published (In production)**, the token auto-refreshes using its
refresh token and keeps working indefinitely.

> ⚠️ If the bot ever fails with `invalid_grant: Token has been expired or revoked.`, the most
> likely cause is that the OAuth consent screen slipped back into **Testing** mode (Google
> hard-expires refresh tokens after 7 days in Testing). Confirm *Publishing status: In production*
> on the OAuth consent screen first (see 1d step 4), **then** regenerate the token below.

To regenerate (after revoking access, switching accounts, or recovering from the above):

```bash
python generate_token.py                              # writes a fresh token.json
gh secret set GOOGLE_OAUTH_TOKEN_B64 --body "$(base64 -i token.json)"
gh workflow run "Deploy Health-Track"                 # roll it out
```

(Or update the `GOOGLE_OAUTH_TOKEN_B64` secret in the repo UI and re-run the latest deploy.)

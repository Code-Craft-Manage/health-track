# Health-Track VPS

> Fill in the connection details once known. Deploy is automatic on push to `main`
> via `.github/workflows/deploy.yml`.

## Connection

| Field | Value |
|---|---|
| IP / host | _set `SERVER_IP` secret_ |
| User | _set `SERVER_USER` secret_ |
| Port | 2244 |
| Code path | `~/htdocs/health-track.codecraftmanage.com` |
| Container | `healthtrack` |

```bash
ssh -p 2244 <user>@<host>
```

## Deploy

Automatic on every push to `main`. To deploy manually from the VPS:

```bash
cd ~/htdocs/health-track.codecraftmanage.com
git pull origin main
docker compose up -d --build
```

## Logs & status

```bash
cd ~/htdocs/health-track.codecraftmanage.com
docker compose logs -f healthtrack      # live logs
docker compose ps                       # status (shows (healthy)/(unhealthy))
docker compose restart healthtrack      # restart
```

## Self-heal

After a 2026-06-28 incident — the container stayed `Up` but silently stopped
polling Telegram, and `restart: unless-stopped` never fired because the process
never exited — the bot now recovers from both failure modes on its own:

- **Heartbeat → healthcheck → autoheal.** The bot rewrites `data/heartbeat`
  every 30s; `healthcheck.py` fails the container probe once it goes stale
  (stalled loop / hung process), and the `autoheal` sidecar restarts the
  unhealthy container. `docker compose ps` shows the `healthtrack` health state.
- **Poller watchdog.** If polling dies while the event loop stays alive (which
  keeps the heartbeat fresh, so the healthcheck can't see it), an in-process job
  exits the bot so `restart: unless-stopped` brings it back.

Both layers just work after a normal deploy; there is nothing extra to run.

## Secrets

All runtime secrets are injected by GitHub Actions at deploy time (forwarded into the deploy
shell and substituted by `docker compose`). **There is no `.env` file on the server.** To change a
secret, update it in **GitHub → repo → Settings → Secrets and variables → Actions**, then push (or
re-run the latest deploy).

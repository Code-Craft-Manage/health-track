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
docker compose ps                       # status
docker compose restart healthtrack      # restart
```

## Secrets

All runtime secrets are injected by GitHub Actions at deploy time (forwarded into the deploy
shell and substituted by `docker compose`). **There is no `.env` file on the server.** To change a
secret, update it in **GitHub → repo → Settings → Secrets and variables → Actions**, then push (or
re-run the latest deploy).

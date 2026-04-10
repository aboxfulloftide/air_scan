# Production Deployment Guide

This documents everything needed to stand up air_scan on a new server.
Currently running on **dev server** — repeat these steps when moving to production.

---

## Prerequisites

- Python 3.12+ with miniforge/conda (or adjust paths in unit files)
- MySQL access to `wireless` DB at 192.168.1.42
- SSH key access (or sshpass) to scanner hosts
- `sshpass` installed (`apt install sshpass`)

---

## 1. Clone & Configure

```bash
git clone <repo> /home/matheau/code/air_scan
cd /home/matheau/code/air_scan
pip install -r requirements.txt
```

Copy and fill in credentials:

```bash
cp .env.example .env
# Set DB_HOST, DB_USER, DB_PASS, DB_NAME, ROUTER_PASS
```

---

## 2. Database

If setting up a fresh DB:

```bash
mysql -h 192.168.1.42 -u networkscan -p wireless < db/setup_db.sql
```

If migrating an existing DB, run all migrations in order:

```bash
mysql -h 192.168.1.42 -u networkscan -p wireless < db/migrate_001_phase1.sql
# ... run all migrations through the latest:
mysql -h 192.168.1.42 -u networkscan -p wireless < db/migrate_014_probe_count.sql
```

---

## 3. System Services

Three services run on the main server. Unit files are in `systemd/`.

| Service | What it does |
|---|---|
| `air-scan-api` | FastAPI/uvicorn web UI + API on port 8002 |
| `air-scan-pull` | Pulls scan data from OpenWrt router every 60s |
| `air-scan-sync` | Syncs known devices from port_scan DB every 5 min |

**Install and start all three:**

```bash
sudo ./systemd/install.sh
```

This copies unit files to `/etc/systemd/system/`, enables them at boot, and starts them.

**Check status:**

```bash
systemctl status air-scan-api air-scan-pull air-scan-sync
```

**View logs:**

```bash
journalctl -u air-scan-api -f
journalctl -u air-scan-pull -f
journalctl -u air-scan-sync -f
```

If Python path differs on production (not miniforge), update the `ExecStart` path in the unit files before running install.sh:

```bash
# Find the right python path:
which python3

# Edit unit files if needed:
sed -i 's|/home/matheau/miniforge3/bin/python3|/usr/bin/python3|g' systemd/*.service
```

---

## 4. Cron Jobs

Two cron jobs belong on the main server:

```bash
# Router watchdog — restarts router capture script if it dies
*/5 * * * * /home/matheau/code/air_scan/scanners/router_watchdog.sh >> /var/log/router_watchdog.log 2>&1

# DB cleanup — deletes observations older than retention period (configured in UI)
0 3 * * * /home/matheau/code/air_scan/scripts/cleanup_observations.sh >> /var/log/air_scan_cleanup.log 2>&1
```

Install both:

```bash
(crontab -l 2>/dev/null; cat <<'EOF'
# --- air_scan router watchdog ---
*/5 * * * * /home/matheau/code/air_scan/scanners/router_watchdog.sh >> /var/log/router_watchdog.log 2>&1
# --- air_scan DB cleanup ---
0 3 * * * /home/matheau/code/air_scan/scripts/cleanup_observations.sh >> /var/log/air_scan_cleanup.log 2>&1
EOF
) | crontab -
```

---

## 5. Deploy Targets

Deploy targets (remote scanner hosts) are configured in `deploy/targets/*.conf`.
Passwords for password-auth targets are stored in `deploy/.secrets` (gitignored).

**After cloning on a new server**, passwords need to be re-entered — they are not in git.
Either:
- Use the web UI: Scanners page → Deploy Targets → edit each sshpass target and re-enter password
- Or manually create `deploy/.secrets`:

```json
{
  "router": "routerpassword",
  "office-pi5": "pi5password"
}
```

---

## 6. Scanner Host (office-pi5)

The pi5 has its own setup — see the systemd unit at `/etc/systemd/system/wifi-scanner.service` on the device.
Scanner files are deployed via the web UI (Scanners → Deploy Targets → Deploy).

The pi5 service reads env from `/home/matheau/scanner/.env` and runs from `/home/matheau/scanner/scanners/`.
This is separate from the air_scan repo checkout — it's a dedicated scanner runtime directory.

---

## 7. OpenWrt Router

The router runs `router_capture.sh` in the background. It is:
- Deployed via the web UI (Scanners → Deploy Targets → Deploy)
- Kept alive by the router watchdog cron
- Controlled manually via `scanners/router_ctl.sh start|stop|status|logs`

See `memory/openwrt-router.md` for full setup details.

---

## Summary Checklist

- [ ] Clone repo, install dependencies, configure `.env`
- [ ] Run `db/setup_db.sql` (fresh) or migration (existing)
- [ ] `sudo ./systemd/install.sh` — installs and starts 3 services
- [ ] Install cron jobs (watchdog + cleanup)
- [ ] Re-enter deploy target passwords in web UI (not in git)
- [ ] Verify: `systemctl status air-scan-api air-scan-pull air-scan-sync`
- [ ] Verify: open `http://<server>:8002` and confirm scanners show online

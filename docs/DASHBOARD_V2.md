# Dashboard v2 (Modern UI)

This document describes the new React + Vite dashboard that runs alongside the original dashboard UI.

Overview

- Served at `/Dashboard` and designed not to break the original dashboard.
- Built with React 18 + Vite, uses Chart.js for charts and a set of components under `dashboard_v2/src/components`.
- Local dev: `npm run dev`; production build: `npm run build` -> `dashboard_v2/dist`.

Pages

- Home (overview)
- VM Manager
- Resource Usage
- Logs Viewer
- Optimizer
- Settings
- API / System Info
- Advanced Tools
- Login

Key Features

- Live host metrics and charts (`/Dashboard/api/stats`).
- VM management: start/stop/restart/delete, per-VM CPU/RAM bars, open VM links, and instance details modal.
- Logs viewer with server-side tailing (`/Dashboard/api/vm/logs/<name>`).
- Per-VM stats endpoint (`/Dashboard/api/vm/stats`) showing container-level CPU/memory/network values (parses `docker stats --no-stream`).
- VM execute endpoint (`/Dashboard/api/vm/exec/<name>`) for running short commands inside `blobevm_<name>` containers (10s timeout, returns stdout/stderr/exit code).
- Optimizer controls (integrates with `optimizer/` service endpoints if present).
- Toast notifications and a `VmExec` UI for running commands from the browser (non-interactive).

Server-side Notes

- Additions live in `dashboard/app.py` and are additive. The existing dashboard remains functional.
- Authentication: v2 uses HMAC-signed tokens issued by `POST /Dashboard/api/auth/login`. Tokens are signed with `DASH_V2_SECRET` (fallback: Flask `SECRET_KEY`).
- The old Basic auth flow continues to work. Server-side auth helpers accept either Basic credentials or a valid v2 token.
- Diagnostics endpoint: `GET /dashboard/api/v2/info` (protected by old auth) reports build presence and `last_error` content.

Important API Endpoints

- `POST /Dashboard/api/auth/login` — issue v2 token (reads admin password from existing dashboard settings).
- `GET /Dashboard/api/auth/status` — validate current v2 token.
- `GET /Dashboard/api/stats` — host/system metrics for charts and overview.
- `GET /Dashboard/api/vm/logs/<name>` — tail logs for `blobevm_<name>` containers.
- `GET /Dashboard/api/vm/stats` — current container stats (parsed `docker stats --no-stream`).
- `POST /Dashboard/api/vm/exec/<name>` — run a short command inside a container (10s timeout).
- `GET /dashboard/api/v2/info` — v2 build presence and `last_error` (protected by old dashboard auth).

Installer & Build Behavior

- `install.sh` now ensures `pip3` and `psutil` (for host metrics) and that a Docker CLI is available (installs `docker.io` if missing). Adjust Docker installation for your distro if needed.
- When `dashboard_v2/package.json` exists, the installer runs `npm ci` and `npm run build` in `dashboard_v2` and captures stderr to `dashboard_v2/last_error.txt` on failure. The server endpoint `GET /dashboard/api/v2/info` reads this file to present build diagnostics in the original dashboard UI.

Recommended workflow
--------------------

There are two ways to keep dashboard_v2 builds reproducible and avoid installer failures:

- **Preferred — commit a lockfile (deterministic installs):**
	- Locally run `npm install` in `dashboard_v2` to generate `package-lock.json`, commit that lockfile to the repository, then the installer can use `npm ci` reliably on target machines.
	- Example:

```bash
cd dashboard_v2
npm install --no-audit --no-fund
git add package-lock.json
git commit -m "chore(dashboard_v2): add package-lock.json for reproducible installs"
```

- **Alternative — allow installer fallback (already implemented):**
	- The installer now attempts `npm ci` and falls back to `npm install` when no lockfile is present. This ensures devDependencies like `vite` are installed so `npm run build` can succeed even if no lockfile exists.

Quick fix on a target machine (what to run on the server)
------------------------------------------------------

If your install failed with `vite: not found`, run these commands on the target machine as root (or with `sudo`) inside `/opt/blobe-vm/dashboard_v2`:

```bash
cd /opt/blobe-vm/dashboard_v2
npm install --no-audit --no-fund
npm run build
tail -n 200 /opt/blobe-vm/dashboard_v2/last_error.txt || true
```

Notes
-----
- `vite` is a devDependency in `dashboard_v2/package.json`; building the production bundle requires it to be installed (either by `npm install` or by having it in node_modules from a prior `npm ci` with a lockfile).
- The installer now appends build stderr to `dashboard_v2/last_error.txt` so the original dashboard can show diagnostics.

Contact me if you want me to either commit a generated `package-lock.json` to the repo or attempt a local build in this workspace to verify the process.

Build & Local Development

```bash
cd dashboard_v2
npm ci
npm run dev   # development server
npm run build # production build -> dashboard_v2/dist
```

Environment & Runtime

- Set `DASH_V2_SECRET` in production for secure v2 tokens (fallback: Flask `SECRET_KEY`).
- `docker` CLI must be available to the Flask process for container-level endpoints.
- `psutil` is used for host metrics if present; the installer ensures it's installed via `pip3`.

Security & Limitations

- `POST /Dashboard/api/vm/exec/<name>` is non-interactive and has a short timeout (10s). For interactive sessions use an SSH/websocket approach.
- The v2 UI reads `new_dashboard_admin_password` from the original dashboard settings file; only the original dashboard should write it.

Troubleshooting

- If the v2 UI does not load, check `dashboard_v2/last_error.txt` for build errors and `GET /dashboard/api/v2/info` for diagnostics (requires original dashboard auth).
- Ensure `DASH_V2_SECRET` is set and that `docker` and `psutil` are accessible to the dashboard process.

Next Steps & Recommendations

- Set `DASH_V2_SECRET` in production env vars.
- Consider adding SSE/WebSocket streaming for interactive logs and terminals.
- Optionally add an `nginx` sample config to `docs/` to show how to route `/Dashboard` and `/dashboard` behind a reverse proxy.

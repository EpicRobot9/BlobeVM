# BlobeVM (Modified DesktopOnCodespaces)

## Codespaces Installation
Start a new blank codespace by going to https://github.com/codespaces/ and choosing the "Blank" template. Then run:
```

For a full description of the new modern dashboard (Dashboard v2), see `docs/DASHBOARD_V2.md` which documents pages, server endpoints, installer behavior, build instructions, environment variables, and troubleshooting tips.

curl -O https://raw.githubusercontent.com/EpicRobot9/BlobeVM/main/install.sh
chmod +x install.sh
./install.sh
```

## Host/VPS Installation (Docker + Traefik)
This repository now includes a server installer and a VM manager to run BlobeVM on a VPS (e.g., Hostinger, any Ubuntu 22.04+/24.04 host).

What you'll get:
- Docker and Traefik reverse proxy
- Optional HTTPS via Let's Encrypt (with your domain)
- A CLI: `blobe-vm-manager` to create/start/stop/delete VM instances
- Automatic Traefik routing: either `https://<name>.<your-domain>/` or path-based `http://<server-ip>/vm/<name>/` when no domain is configured
- A web dashboard (auto-deployed) at `/dashboard` (and optionally `dashboard.<your-domain>`)

### 1) Quick one-line install
Run this on your server to clone the repo and start the guided installer:
```
curl -fsSL https://raw.githubusercontent.com/EpicRobot9/BlobeVM/main/install-blobevm.sh | sudo bash
```

Need to avoid Traefik and any currently allocated ports? Use direct mode (no proxy):
```
curl -fsSL https://raw.githubusercontent.com/EpicRobot9/BlobeVM/main/install-blobevm.sh | sudo BLOBEVM_NO_TRAEFIK=1 BLOBEVM_ENABLE_DASHBOARD=1 BLOBEVM_DIRECT_PORT_START=20000 bash
```
In direct mode, each VM is exposed on a unique high port automatically (starting at BLOBEVM_DIRECT_PORT_START, default 20000). The CLI prints the exact URL (for example, http://<server-ip>:20017/). The dashboard, if enabled, is served directly on another free high port.

Or run the installer from a local clone:
### Alternative: Run the server installer from this repo
From the server where this repo is present:
```
sudo bash server/install.sh
```
The installer will:
- Install Docker and Traefik, create a `proxy` network (unless BLOBEVM_NO_TRAEFIK=1)
- Build the BlobeVM image
- Install the `blobe-vm-manager` CLI
- Deploy the web dashboard automatically (set `DISABLE_DASHBOARD=1` before running to skip). In direct mode the dashboard is published on a free high port.
- Optionally create your first VM and print its URL

Notes during install:
- If ports 80/443 are already in use on the host, the installer will show which process is using the port and ask whether to kill it or switch to another port (auto-picking from 8080/8443 upward when you choose switch). In that case, URLs will include the chosen port (e.g., http://<server-ip>:8880/vm/<name>/). The CLI output reflects the correct port.
- If you set BLOBEVM_NO_TRAEFIK=1, Traefik is skipped entirely and no privileged ports are used; each VM gets a unique high port to avoid conflicts.
- Re-running the installer on an existing server safely updates the image, CLI, and Traefik config without deleting existing VMs. You'll be offered to reuse your current settings and any existing Traefik instance/network.

If you provide a domain and email, Traefik will request certificates via Let's Encrypt. Point DNS for `*.your-domain` and `traefik.your-domain` to your server IP before use.

### 2) Manage VMs
```
# List VMs
blobe-vm-manager list

# Direct mode helpers
blobe-vm-manager list-ports          # Show VM -> port mapping
blobe-vm-manager port alpha          # Show the port for a specific VM
blobe-vm-manager set-port alpha 20123# Set a fixed port and recreate the container

# Quick URL helpers
blobe-vm-manager url alpha           # Print URL for the VM
blobe-vm-manager open alpha          # Attempt to open URL in a browser
blobe-vm-manager dashboard-url       # Print dashboard URL
blobe-vm-manager open-dashboard      # Attempt to open dashboard in a browser

# Create a VM named "alpha"
blobe-vm-manager create alpha

# Start/Stop
blobe-vm-manager start alpha
blobe-vm-manager stop alpha

# Restart a VM (new)
blobe-vm-manager restart alpha

# Verify a VM is running
# 1) Via manager
blobe-vm-manager status alpha    # prints container status and URL
# 2) HTTP check via manager
blobe-vm-manager check alpha           # returns OK/FAIL; auto-resolves common 404s by recreating
blobe-vm-manager check --no-fix alpha  # report-only (no auto-resolve)
# 3) Docker (optional)
docker ps --filter name=blobevm_alpha --format '{{.Names}} {{.Status}}'

# Delete
blobe-vm-manager delete alpha
```
After create/start, the CLI prints the VM URL.

### 3) Switching VM URLs
Domain mode (you provided a domain + email during install):
```
# Change the subdomain by renaming the instance
blobe-vm-manager rename oldname newname

# Use a custom FQDN instead of name.your-domain
blobe-vm-manager set-host myvm vm42.example.com

# Revert to default host (name.your-domain)
blobe-vm-manager clear-host myvm
```



# Interactive helper to set a host (shows IPs first)
blobe-vm-manager set-host-interactive myvm

Path mode (no domain configured):
```
# Change the default path by renaming the instance (/vm/newname/)
blobe-vm-manager rename oldname newname

# Use a custom path prefix (URL becomes http://<server-ip>/desk/42/)
blobe-vm-manager set-path myvm /desk/42

# Revert to default path (/vm/<name>/)
blobe-vm-manager clear-path myvm
```

# Dual access: Even when a host override or domain is used, each VM is still reachable via the path form (default /vm/<name>/ or custom base path) unless you remove the path router manually.

### 4) Global base path
You can change the shared base path for path routing (default /vm):
```
blobe-vm-manager set-base-path /desktops
blobe-vm-manager clear-base-path   # back to /vm
```
After changing the base path, containers are recreated and new path URLs take effect (host overrides still work and keep dual access).

### 5) Resource limits per VM
You can constrain CPU and memory for a VM:
```
blobe-vm-manager set-limits myvm 1.5 2g   # 1.5 CPUs, 2 GiB RAM
blobe-vm-manager clear-limits myvm
```
Values:
- CPU: fractional or integer (Docker --cpus semantics)
- Memory: Docker format (e.g., 512m, 2g)

### 6) HTTPS redirect & dashboard auth
During installation you can enable:
- Force HTTP→HTTPS redirect (requires email for ACME certs)
- Basic auth on Traefik dashboard (Path `/traefik`)
If you skipped these, you can re-run the installer or manually edit `/opt/blobe-vm/traefik/docker-compose.yml`.

### 7) Web Dashboard
The dashboard is deployed by default and available at:
```
http://<server-ip>/dashboard
```
If you configured a domain and set a host override for it (future enhancement) or added DNS manually, you can expose it at `dashboard.<your-domain>`.

Actions supported in UI:
- List instances (auto-refresh)
- Create a VM
- Start/Stop/Delete a VM
- Restart and Check a VM (HTTP check with auto-resolve; Shift+Click for report-only)
- Update a VM (apt update/dist-upgrade inside the VM container; preserves /config)
- App controls: Install Chrome in a VM; Install App… and App Status… prompts for any available app script
- Bulk ops: Recreate/Rebuild/Update & Rebuild/Delete ALL VMs

Quick restart of the dashboard (systemd):
```
sudo systemctl restart blobedash
sudo systemctl status blobedash --no-pager -l
```

Disable dashboard on fresh install:
```
DISABLE_DASHBOARD=1 sudo bash server/install.sh
```
Remove after install:
Dashboard internal auth (optional):
Set credentials before (re)deploying the dashboard so all UI/API requests require them:
```
export BLOBEDASH_USER=admin
export BLOBEDASH_PASS='StrongPassword123'
sudo bash server/install.sh   # or: docker compose restart dashboard after editing compose
```
If already deployed, update the env vars in `/opt/blobe-vm/traefik/docker-compose.yml` under the `dashboard` service and run:
```
cd /opt/blobe-vm/traefik
docker compose up -d dashboard
```
```
cd /opt/blobe-vm/traefik
docker compose rm -sf dashboard
sed -i '/dashboard:/,/^$/d' docker-compose.yml
```

### Uninstall (nuke)
To remove all BlobeVM instances, Traefik, data, images, and the CLI:
```
blobe-vm-manager nuke
```
You’ll be prompted to confirm.

Notes:
- KVM passthrough can be enabled if `/dev/kvm` is present on the host and selected during install.
- Data for each VM lives under `/opt/blobe-vm/instances/<name>/config`.
- Dynamic DNS (e.g., No-IP): You can map a No-IP hostname to any VM using `blobe-vm-manager set-host <vm> <hostname>`. Ensure the hostname resolves to your server’s IP. If you provided an email during install, Traefik will request HTTPS certificates automatically for that hostname when first accessed (HTTP-01 challenge).
- Dual access (host + path): When a VM has a host route (custom host or domain), a path route still exists so you can reach it via both forms unless you tailor Traefik labels manually.
- Per-VM limits: Use `set-limits` to avoid a single VM consuming all host resources.
- Dashboard: Provides a quick management UI; disable by setting `DISABLE_DASHBOARD=1` before install or removing the service from the compose file.

## CLI Quick Reference

### Core lifecycle
```
blobe-vm-manager list                  # Show all VMs and their state
blobe-vm-manager create <name>         # Create a new VM instance
blobe-vm-manager start <name>          # Start a VM
blobe-vm-manager stop <name>           # Stop a VM
blobe-vm-manager delete <name>         # Delete a VM (removes data)
blobe-vm-manager rename old new        # Rename a VM (updates URLs)

### Rebuild/update utilities
```
blobe-vm-manager pull-repo             # git pull in the server repo (if present)
blobe-vm-manager rebuild-image         # rebuild the BlobeVM Docker image from REPO_DIR
blobe-vm-manager recreate-all          # recreate all VM containers using the current image
blobe-vm-manager recreate vm1 vm2      # recreate only the specified VMs
blobe-vm-manager rebuild-all           # rebuild image and recreate all VMs
blobe-vm-manager rebuild-vms vm1 vm2   # rebuild image, then recreate specified VMs
blobe-vm-manager delete-all-instances  # delete ALL VMs and their data (keeps image/stack)
blobe-vm-manager update-and-rebuild    # pull repo, rebuild image, recreate all VMs
blobe-vm-manager update-and-rebuild vm1 vm2  # pull repo, rebuild image, recreate only these VMs
```
```

### VM maintenance and app controls
```
blobe-vm-manager update-vm <name>           # apt update/dist-upgrade inside the VM
blobe-vm-manager app-install <name> chrome  # install Google Chrome inside the VM
blobe-vm-manager app-status <name> chrome   # check if Chrome is installed in the VM
```

To add a new app:
- Create a script at `root/installable-apps/<app>.sh` in this repo. The script runs as root inside the VM container.
- Keep it idempotent (safe to re-run) and non-interactive.
- After deploying, the dashboard will list it under the Install App… prompt automatically.

### Routing controls
```
blobe-vm-manager set-host <vm> <fqdn>  # Serve a VM at a custom hostname
blobe-vm-manager clear-host <vm>       # Revert to default host routing
blobe-vm-manager set-path <vm> /desk/7 # Serve a VM at a custom path prefix
blobe-vm-manager clear-path <vm>       # Revert to default path routing
blobe-vm-manager set-base-path /desk   # Change global base path (default /vm)
blobe-vm-manager clear-base-path       # Reset global base path to /vm
```

### Resource limits
```
blobe-vm-manager set-limits <vm> <cpus> <memory>
blobe-vm-manager set-limits myvm 1.5 2g  # Example (1.5 CPUs, 2 GiB RAM)
blobe-vm-manager clear-limits <vm>       # Remove limits
```

### HTTPS, redirects, and dashboard auth
- Re-run `server/install.sh` (or the one-line installer) with HTTPS inputs to enable TLS/Let's Encrypt. Set `BLOBEVM_FORCE_HTTPS=1` to force HTTP→HTTPS redirects once TLS is active. (Not applicable in direct mode.)
- Protect the dashboard by exporting `BLOBEDASH_USER` and `BLOBEDASH_PASS` (or answering the prompt) before running the installer. Credentials are stored in Traefik labels.

### Dashboard management
- Skip deployment on install: `DISABLE_DASHBOARD=1 sudo bash server/install.sh`
- Redeploy after removal: `docker compose up -d dashboard` inside `/opt/blobe-vm/traefik`
- Remove after install: `cd /opt/blobe-vm/traefik && docker compose rm -sf dashboard`

Direct mode (no Traefik): If enabled, the dashboard runs as a standalone container on a free high port. The installer prints the URL. You can stop/start it via `docker stop blobedash` / `docker start blobedash`.

Direct mode with systemd (auto-manage dashboard):
- The installer installs a systemd unit `blobedash.service` that ensures the dashboard container exists and runs on a free high port.
- Manage it via:
```
sudo systemctl start blobedash
sudo systemctl status blobedash
sudo systemctl restart blobedash   # re-evaluates/assigns port if needed
sudo systemctl stop blobedash
```

### Full uninstall
```
blobe-vm-manager nuke   # Removes all VMs, data, Traefik stack, and CLI (prompts for confirmation)
```

# BlobeVM Dashboard Features

The web dashboard provides a modern UI for managing VMs and switching routing modes. Key features:

- **Auto-refresh VM list**: See all VMs, their status (with color dot), and quick actions.
- **Create, Start, Stop, Delete VMs**: One-click controls for lifecycle management.
- **Open buttons**: Each VM row has an "Open" button that adapts to the current routing mode:
  - **Merged mode**: VMs are available at `/vm/<name>` on a single port (e.g., `http://your-ip:20002/vm/alpha`).
    In merged mode, even if you set a domain, VM routes are path-based under the shared port to avoid 404s on fresh containers.
  - **Direct mode**: Each VM is exposed on a unique high port (e.g., `http://your-ip:20017/`).
- **Port/Path display**: Shows the port (direct mode) or merged path (merged mode) for each VM.
- **Status dot**: Green (running), red (stopped/exited), gray (unknown).
- **Toggle routing modes**:
  - **Enable single-port (merged) mode**: Enter a port (e.g., 20002) and click to route all VMs and the dashboard via Traefik on that port.
  - **Disable single-port (revert to direct)**: Optionally pick a dashboard port, then click to switch back to direct mode (each VM gets a high port).
- **Custom domain for merged mode**:
  - Enter your domain (e.g., `vms.example.com`) and the dashboard will show the IP to point your DNS A record to.
  - All VM links and "Open" buttons will use your domain if set.

## Example workflow

1. **Switch to merged mode**: Enter a port (e.g., 20002) and click "Enable single-port mode". All VMs and the dashboard will be available at that port.
2. **Set your domain**: Enter your domain in the "Custom domain" field. The dashboard will show the IP to point your DNS A record to. Update your DNS provider accordingly.
3. **Open VMs**: Use the "Open" button for each VM. In merged mode, links use your domain if set.
4. **Revert to direct mode**: Click "Disable single-port (direct mode)" to switch back. Each VM will be available on its own high port.

## Troubleshooting
- If a port is busy, the dashboard will prompt you to choose another.
- DNS changes may take time to propagate. Use the IP shown in the dashboard for your A record.
- You can always switch modes and update your domain as needed.
- Enable browser debug logs by appending `?debug=1` to the dashboard URL, then open the browser console to see messages prefixed with `[BLOBEDASH]`.

---

**Favicon Customization**

- **What it does:** Configure a global dashboard favicon and per-VM favicons. If a VM does not have its own favicon, the dashboard falls back to the global favicon (if present).

- **UI:** The dashboard includes a "Dashboard Settings" panel where you can:
  - set the dashboard title,
  - provide a favicon URL, or
  - upload a favicon file directly.
  Each VM row also has an "Upload Favicon" button to set a per-VM favicon.

- **APIs / routes:**
  - `GET /dashboard/api/settings` : Read current settings (JSON).
  - `POST /dashboard/api/settings` : Save `title` and/or `favicon` (form-encoded). `favicon` may be a URL.
  - `POST /dashboard/api/upload-favicon` : Upload global favicon (multipart form, field `file`).
  - `POST /dashboard/api/upload-vm-favicon/<name>` : Upload per-VM favicon (multipart form, field `file`).
  - `GET /dashboard/favicon.ico` : Serve global favicon (public).
  - `GET /dashboard/vm-favicon/<name>.ico` : Serve per-VM favicon or redirect to global favicon (public).

- **Storage:**
  - Settings JSON: `<state_dir>/dashboard_settings.json` (default `BLOBEDASH_STATE` or `/opt/blobe-vm`).
  - Global favicon file: `<state_dir>/dashboard/favicon.ico` (preferred if present).
  - Per-VM favicons: `<state_dir>/dashboard/vm-fav/<vmname>.ico`.

- **Auth:** Most API routes require dashboard basic auth when `BLOBEDASH_USER`/`BLOBEDASH_PASS` are set. Static favicon routes are public to avoid browser 401s.

- **Examples:**
  - Set title and favicon URL via API:
    ```bash
    curl -X POST -d "title=My+Lab&favicon=https://example.com/myfav.ico" \
      http://localhost:5000/dashboard/api/settings
    ```
  - Upload a global favicon file:
    ```bash
    curl -X POST -F "file=@/path/to/favicon.ico" http://localhost:5000/dashboard/api/upload-favicon
    ```
  - Upload a per-VM favicon:
    ```bash
    curl -X POST -F "file=@/path/to/vm-fav.ico" http://localhost:5000/dashboard/api/upload-vm-favicon/myvm
    ```

- **Notes & behavior:**
  - The server will try to download a favicon when you submit a remote URL and save it locally when possible; otherwise the URL is stored and referenced directly.
  - Browsers accept PNG or ICO files as favicons; prefer using an `.ico` for widest compatibility. The server saves bytes as-is.
  - No container restart is required — the dashboard will pick up changes after a short reload. If you run the dashboard as a daemonized container, ensure the `BLOBEDASH_STATE` volume is mounted so saved files are persistent.


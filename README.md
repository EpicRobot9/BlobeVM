# BlobeVM (Modified DesktopOnCodespaces)

## Codespaces Installation
Start a new blank codespace by going to https://github.com/codespaces/ and choosing the "Blank" template. Then run:
```
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
bash -c "$(curl -fsSL https://raw.githubusercontent.com/EpicRobot9/BlobeVM/main/server/quick-install.sh)"
```

Or run the installer from a local clone:
### Alternative: Run the server installer from this repo
From the server where this repo is present:
```
sudo bash server/install.sh
```
The installer will:
- Install Docker and Traefik, create a `proxy` network
- Build the BlobeVM image
- Install the `blobe-vm-manager` CLI
- Deploy the web dashboard automatically (set `DISABLE_DASHBOARD=1` before running to skip)
- Optionally create your first VM and print its URL

Notes during install:
- If ports 80/443 are already in use on the host, the installer will show which process is using the port and ask whether to kill it or switch to another port (auto-picking from 8080/8443 upward when you choose switch). In that case, URLs will include the chosen port (e.g., http://<server-ip>:8880/vm/<name>/). The CLI output reflects the correct port.
- Re-running the installer on an existing server safely updates the image, CLI, and Traefik config without deleting existing VMs. You'll be offered to reuse your current settings and any existing Traefik instance/network.

If you provide a domain and email, Traefik will request certificates via Let's Encrypt. Point DNS for `*.your-domain` and `traefik.your-domain` to your server IP before use.

### 2) Manage VMs
```
# List VMs
blobe-vm-manager list

# Create a VM named "alpha"
blobe-vm-manager create alpha

# Start/Stop
blobe-vm-manager start alpha
blobe-vm-manager stop alpha

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

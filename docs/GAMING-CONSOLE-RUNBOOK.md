# EpicVM Gaming Console — Operations Runbook

How to provision, repair, and verify a Gaming VM console end-to-end, and
what to do when each stage fails. Written from the Aug 22 2026 recovery
(local WSL2 simulation + production kvm2 run).

## The happy path

1. **Provision** — `POST /dashboard/api/provisioning-jobs` with
   `{host_id, name, profile: "gaming", mode: "automatic"}`. The dashboard
   forwards to the Windows agent (`/v1/provisioning-jobs`). Stages run in
   order: `claim → guest_setup → network_setup → management_handoff →
   gaming_gpu → streaming_setup`.
2. **Console staging (dashboard-side, async)** — the orchestrator writes
   `$EPICVM_MOONLIGHT_ROOT/<vm>--<host>/` (compose + plan.json), starts the
   bundle via `docker compose -p epicvm-<name>--<host> up -d --wait`, pairs
   it with Sunshine (PIN dance), then calls the agent's
   `POST /v1/provisioning-jobs/<id>/console-complete`.
3. **Readiness** — the agent flips the job to `ready` ONLY after
   `console-complete` carries route + TCP + **quantified frame metrics**
   (nonblackFraction ≥ 0.60, meanLuma ≥ 12, stdDev ≥ 8, decodedFramesDelta
   ≥ 3, durationMs ≥ 1500). This is deliberate. Do not bypass it.

## Retry paths

| Failure | Endpoint | Notes |
|---|---|---|
| Streaming stage failed, capture not configured | agent `POST /v1/provisioning-jobs/<id>/console-credentials` | body: `username`, `password` (guest), `sunshineUsername`, `sunshinePassword`. Note: guest creds are `username`/`password`, NOT `guestUsername`. |
| Bundle missing/stale, capture already configured | dashboard `POST /dashboard/api/provisioning-jobs/<id>/repair-console` | same credential field names as above; requires `X-Forwarded-Proto: https` + CSRF header. Runs async (202). |
| Everything green, need to flip state | agent `POST .../console-complete` | must carry REAL frame metrics from a browser session. |

## Failure catalog (seen in production)

- **`gaming_capacity`** — only one Gaming VM may run. Stop the old one
  first (`POST /v1/vms/<name>/stop`). Stale `setup_failed:streaming` jobs
  still count.
- **`host_unavailable` from the dashboard** — usually NOT connectivity.
  Two known causes: (a) remote-hosts registry token not AES-GCM sealed
  (`EV1:` prefix) — the dashboard silently drops the host; (b) the agent
  was mid-restart. Check `/dashboard/api/hosts` → `online`.
- **`digest_required`** — `EPICVM_MOONLIGHT_IMAGE` must be a digest pin
  (`...@sha256:64hex`). After building a new overlay image, pin it:
  `docker image inspect <img> --format '{{index .RepoDigests 0}}'` and put
  that exact string in `.env` / container env.
- **`route_collision` / port 41000 already allocated** — every bundle
  publishes UDP 41000-41010. Only one bundle can run at a time per port
  range. Remove stale bundles: `docker rm -f <old-bundle>` and
  `docker compose -p <old-project> down` in its instance dir.
- **`console_start_failed`** — compose up failed. Run
  `docker compose -p <project> up -d --wait` by hand in
  `/opt/epicvm/moonlight-instances/<name>/` to see the real error.
- **`sunshine_pair_failed`** — Sunshine PINs expire fast. The pair POST and
  the PIN submission must happen within seconds of each other. Retry the
  whole repair rather than reusing a PIN.
- **`frame_evidence_rejected`** — the metrics were real and the stream was
  bad (black/frozen). Do not retry with different numbers; fix the stream.
- **WinRM 413/envelope errors** — payloads over ~500KB fail. VDD artifacts
  are chunked at 200KB by the capture script; if you add artifacts, keep
  chunks under that.

## Local integration notes (WSL2)

- Mirrored networking + `hostAddressLoopback=true` in `.wslconfig`.
- The dashboard launcher must set: `BLOBEDASH_STATE` (a DIRECTORY),
  `DASH_V2_SECRET`, `BLOBEDASH_USER`, `BLOBEDASH_PASS`,
  `BLOBEVM_USER_SECRET`, `EPICVM_REMOTE_HOSTS_FILE`,
  `EPICVM_PROVISIONING_CREDENTIALS_FILE`, `EPICVM_MOONLIGHT_ROOT`,
  `EPICVM_CONSOLE_BACKEND=moonlight`, `EPICVM_PUBLIC_HOST`.
- WSL distro bounces kill the flask process but NOT docker containers
  (restart policy revives them). After a bounce: re-launch the dashboard
  and re-seal `remote-hosts.json` tokens (they must carry the `EV1:`
  prefix).
- Local ICE caveat: the moonlight container advertises docker-bridge
  candidates (172.x) that a WSL-side browser cannot reach. Production
  avoids this with `WEBRTC_NAT_1TO1_HOST=<public-ip>`. Local pixel proof
  needs the browser on the same host as docker, or host networking.

## Prod deploy checklist (kvm2)

1. `cd /opt/blobe-vm/repo && git pull --ff-only origin production`
2. Copy changed dashboard files: `cp repo/dashboard/*.py /opt/blobe-vm/dashboard/`
   (backup first — `.dashboard-backups/<date>/`).
3. Rebuild the overlay if `docker/moonlight-web/patch_stream.py` changed:
   `cd repo/docker/moonlight-web && docker build -t epicvm/moonlight-web:<tag> .`
   then digest-pin it in `/opt/blobe-vm/.env`.
4. Recreate `blobedash` (original image `blobedash:webrtc-udp-0f31510-r2`
   has flask baked in; bare python:3.11-slim does NOT work):
   see `.dashboard-backups` / `server/install.sh` for the exact
   `docker run` shape (network `proxy`, port `20000:5000`, mounts for
   /opt/blobe-vm, /opt/epicvm, docker socket, dashboard:/app:ro).
5. Health: `curl :20000/dashboard/api/auth/csrf` → 401 (alive, gated).
6. Confirm no stray qemu/KVM VMs: `ps aux | grep qemu-system` (kvm2 has no
   virsh; gaming VMs run on the Windows agent host, not on kvm2).

## Session auth for automation (prod)

Login needs the operator password (only PASS_HASH is stored — password not
recoverable). For scripts, mint a session token in-process instead:

```python
# inside the blobedash container, as the same user running the app
import base64, hashlib, hmac, os, time, secrets
secret = os.environ["DASH_V2_SECRET"]
payload = f"{int(time.time())+86400}:{secrets.token_hex(16)}"
mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
token = base64.urlsafe_b64encode(f"{payload}:{mac}".encode()).decode()
# -> Cookie: Dashboard-Auth=<token>
```

CSRF: `GET /dashboard/api/auth/csrf` with that cookie; send the token back
as `X-CSRF-Token`. Mutating endpoints also require
`X-Forwarded-Proto: https` (or go through the real TLS edge).

## Evidence rules (non-negotiable)

`console-complete` frame metrics must come from an actual browser session
rendering the live stream. Fabricated metrics are the exact defect this
system was rebuilt to reject. If the stream is black, fix the stream.

## WSL-bundle video path (Aug 23 2026 — LIVE, pixel-verified)

```
browser ──https──> Cloudflare ──> kvm2 Traefik (auth chain unchanged)
                                      │ file-provider router (prio 650)
                                      │   /opt/bloe-vm/traefik/dynamic/
                                      │     epicvm-gaming-verify-1-wsl.yml
                                      ▼
                        http://100.72.220.117:18080  (WSL bundle, host-net)
                                      │ Moonlight RTSP+ENet+media (tailnet)
                                      ▼
              Sunshine on prod-gaming-verify-1 (Hyper-V, this PC)
                 100.74.55.92  ports 47984/47989/47990/48010 + UDP 47998-48000
                 (IP changed from 100.109.155.25 after a tailscale re-enroll;
                  both bundles' server/data.json were updated to match)

Remote users: WebRTC falls back to TURN on kvm2.
  turn:72.60.29.204:3478 (public) AND turn:100.89.87.98:3478 (tailnet) are
  both configured in the bundle's ice_servers; provider firewall now allows
  inbound 3478 tcp/udp (verified via authenticated allocation from WSL).
  Relay range 49160-49200/udp published on kvm2 docker.
  Long-term creds: kvm2:/root/.turncreds. Maintenance:
  `ssh kvm2 'docker restart coturn'`; rotate creds by editing
  /root/.turncreds then recreating the container with the new -u.
```

Live verification (Aug 23 04:5x UTC): public page → forwardauth OK → WHEP
session up → `videoWidth=1920`, frames 30→201 in 8 s; 8 screenshots over
~11 s gave nonblack ≥ 0.970, meanLuma ≥ 83.8, stdDev ≥ 45.2 (thresholds
0.60/12/8) → PASS.

Verified working pieces:

- WSL bundle `epicvm-prod-gaming-verify-1-local-moonlight-web-1` runs with
  `network_mode: host`, HTTP bound directly on `0.0.0.0:18080`
  (`/opt/epicvm/moonlight-instances/prod-gaming-verify-1/docker-compose.yml`;
  pre-change copies in `/root/compose.yml.bak-*` inside WSL).
  Host networking is required for predictable source addressing.
- Pairing survived the verbatim `server/config.json` + `server/data.json`
  copy AND a guest reboot: `/api/host?host_id=3593014841` stays `Paired`.
- kvm2 Traefik runs BOTH providers; docker-labels router on the kvm2-side
  console container (priority 600) vs file router (650). The file router
  currently serves prod traffic; its exact copy also lives at
  `kvm2:/root/epicvm-gaming-verify-1-wsl.yml.bak-20260822`.
- coturn on kvm2 (`docker run -d --name coturn -p 3478:3478/tcp
  -p 3478:3478/udp -p 49160-49200:49160-49200/udp coturn/coturn:latest -n
  --lt-cred-mech --realm=techexplore.us --min-port=49160 --max-port=49200
  --external-ip=72.60.29.204 --listening-ip=0.0.0.0 --no-cli
  -u "$TURN_USER:$TURN_PASS"`). Auth + relay echo verified 0% loss via
  turnutils_uclient/turnutils_peer inside the container. Maintenance:
  `ssh kvm2 'docker restart coturn'`; rotate creds by editing
  `/root/.turncreds` then recreating the container.

### Rollback (one command)

```
ssh kvm2 'rm -f /opt/bloe-vm/traefik/dynamic/epicvm-gaming-verify-1-wsl.yml'
```

Traefik watches `/dynamic`; the docker-labels router takes over instantly
(the kvm2-side console project is now `prod-gaming-verify-1` with container
`prod-gaming-verify-1-moonlight-web-1` after the Aug 23 repair re-stage).
Re-cutover = copy `/root/epicvm-gaming-verify-1-wsl.yml.bak-20260822` back
into `/opt/bloe-vm/traefik/dynamic/`.

### Guest-side incident log (Aug 23) — read before touching the VM

1. **Zombie sessions**: aborted sessions leave Sunshine `Busy` /
   `current_game:<id>` and every later launch fails ("Failed to start the
   specified application" or rtsp 500). Clear with

   ```
   curl -X POST -H 'X-EpicVM-User: prod-gaming-verify-1' \
     -H 'Content-Type: application/json' \
     -d '{"user":"<moonlightUserId>","host_id":3593014841}' \
     http://<bundle>:18080/vm/prod-gaming-verify-1--epic-pc/api/host/cancel
   ```

2. **Tailscale logout**: the guest tailnet node dropped to NeedsLogin
   (adapter Up but no 100.x IP) → total media loss while TCP probes kept
   half-working during the transition. Re-enroll without the agent:
   decrypt `C:\ProgramData\EpicVM\agent\tailscale-oauth.dpapi`
   (LocalMachine DPAPI), mint a one-use key
   (`POST /api/v2/oauth/token` then `POST /api/v2/tailnet/-/keys` with
   tag epicvm-guest), then run INSIDE the guest
   `tailscale up --auth-key <key> --hostname prod-gaming-verify-1
   --unattended=true --accept-dns=false --reset`.
   A logout wipes node identity → NEW tailscale IP (was
   100.109.155.25 → now 100.74.55.92). Update BOTH bundles'
   `server/data.json` host address afterwards and restart them.
3. **Guest firewall**: media pings need UDP 47998/48000 inbound. Added
   rules `EpicVM Sunshine Tailnet Range UDP/TCP` (47984-48010 from
   100.64.0.0/10) inside the guest alongside the provisioning defaults.
4. **Wedged capture/display**: after repeated crashes the VDD desktop fell
   back to 800×600 and h264_amf entered an encoder create-loop (one frame
   then freeze). A **guest VM restart** (dashboard
   `POST /dashboard/api/restart/<name>` → agent lifecycle) cleared it;
   pairing SURVIVED the reboot. If pairing ever drops,
   `POST /dashboard/api/provisioning-jobs/<job_id>/repair-console`
   (job `477ed0d485f44e19903dd777a2fbf507`) re-stages and re-pairs — note
   it re-creates the kvm2 console under compose project
   `prod-gaming-verify-1` and may leave it Created-but-not-started; start
   with `docker compose -p prod-gaming-verify-1 up -d --wait`.

App-ID note for this VM's Sunshine: `Desktop` (881448767) and
`Steam Big Picture` (1093255277) both stream. BP required: installing Steam
in the guest (`SteamSetup.exe /S`), setting its app cmd to the REAL exe —
`"C:\Program Files (x86)\Steam\steam.exe" -bigpicture` (quoted; protocol
URLs like `steam://open/...` cannot be spawned by Sunshine, and an unquoted
spaced path fails with Permission denied) — AND an active console session:
Sunshine spawns apps into the console session, so a headless VM at the
lock screen fails with "Permission denied". Keep the session alive with
`tscon <id> /dest:console` after connecting it via PS-Direct/quser.

Public TURN status: provider firewall was opened Aug 23 (3478 tcp/udp
reachable; authenticated allocation verified from an external vantage).
Keep the "don't add unreachable TURN candidates" rule in mind for ANY
future relay: dead candidates add ~4 s of ICE gathering delay each, which
pushes Moonlight's media pings past Sunshine's Initial-Ping window and
black-screens sessions.

### WSL availability hazard (operational)

WSL idle-shutdown (default `vmIdleTimeout` 60 s) stops docker and takes the
video path down. Fixed persistently in `C:\Users\Epic\.wslconfig`
(`[wsl2] vmIdleTimeout=-1`). During ops sessions also keep a holder:
`Start-Process -WindowHidden wsl -ArgumentList '-d Ubuntu --exec sleep 14400'`.
Symptom of a bounce: kvm2→100.72.220.117 curls time out for ~1 min while
containers restart under policy.

### Mirrored-networking caveats (measured Aug 22/23)

- Inbound UDP to WSL listeners works when targeted at the tailnet IP
  (100.72.220.117) but NOT at other host IPs (e.g. LAN 192.168.1.178):
  kvm2→100.72.220.117:48000 delivers; kvm2→192.168.1.178:48000 times out.
  Any component advertising a non-tailnet host IP for return traffic will
  black-hole. Keep everything pinned to
  `WEBRTC_NAT_1TO1_HOST=100.72.220.117`.
- WSL `/proc/net/tcp` does not show mirrored connections reliably; use
  Windows-side `Get-NetTCPConnection`/pktmon or WSL tcpdump on eth1.

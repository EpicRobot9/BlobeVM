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

## WSL-bundle video path (Aug 22 2026 cutover — PREPARED, NOT LIVE)

Intended topology once the cutover is re-attempted and verified:

```
browser ──https──> Cloudflare ──> kvm2 Traefik (auth chain unchanged)
                                      │ file-provider router
                                      │   epicvm-gaming-verify-1-wsl.yml
                                      ▼
                        http://100.72.220.117:18080  (WSL bundle, host-net)
                                      │ Moonlight (TCP+UDP)
                                      ▼
                     Sunshine on prod-gaming-verify-1 (Hyper-V, this PC)
                        100.109.155.25  ports 47984/47989/47990

Remote users: WebRTC falls back to TURN on kvm2.
  turn:72.60.29.204:3478 (public) / turn:100.89.87.98:3478 (tailnet)
  relay range 49160-49200/udp, long-term creds in kvm2:/root/.turncreds.
  NOTE: provider firewall currently BLOCKS inbound UDP/TCP 3478 from the
  internet (works over tailnet). Open 3478 tcp/udp + 49160-49200/udp at
  the provider before promising remote-user support.
```

Prepared state (all verified working):

- WSL bundle `epicvm-prod-gaming-verify-1-local-moonlight-web-1` runs with
  `network_mode: host`, HTTP bound directly on `0.0.0.0:18080`
  (`/opt/epicvm/moonlight-instances/prod-gaming-verify-1/docker-compose.yml`;
  pre-change copies in `/root/compose.yml.bak-*` inside WSL).
  Host networking is required: bridge NAT broke the upstream media leg.
- Pairing survived the verbatim `server/config.json` + `server/data.json`
  copy: `/api/host?host_id=3593014841` reports `Paired` without re-pairing.
- kvm2 Traefik has BOTH providers enabled; the docker-labels router on the
  old kvm2 bundle (priority 600) and a file router (priority 650) can point
  at either backend without touching `/opt/blobe-vm` app code. The prepared
  file-router lives at `kvm2:/root/epicvm-gaming-verify-1-wsl.bak-20260822`.
- coturn runs on kvm2 (`docker run ... coturn/coturn:latest -n --lt-cred-mech
  --realm=techexplore.us --min-port=49160 --max-port=49200 --external-ip=
  72.60.29.204 -u "$TURN_USER:$TURN_PASS"`). Allocation verified with
  turnutils_uclient through auth + relay echo (0% loss). Maintenance:
  `ssh kvm2 'docker restart coturn'`; rotate creds by editing
  `/root/.turncreds` then recreating the container with the new `-u`.
- WSL bundle `server/config.json` ice_servers carry STUN + the two TURN URLs.

### Rollback (one command, any time)

```
ssh kvm2 'rm -f /opt/bloe-vm/traefik/dynamic/epicvm-gaming-verify-1-wsl.yml'
```

Traefik watches `/dynamic`; the docker-labels router on the old kvm2 bundle
(`epicvm-prod-gaming-verify-1-moonlight-moonlight-web-1`, left running)
takes over immediately. Re-cutover = copy the backup yml back into
`/opt/blobe-vm/traefik/dynamic/`.

### Why the Aug 22 cutover attempt stopped (blocker, not rollback-worthy)

End-to-end test got as far as: page loads via public chain → forwardauth OK
→ WHEP POST accepted → browser ICE **connected** (host + TURN-relay
candidates both worked). Then zero RTP: the bundle never logged
"received first video packet" from Sunshine, while the identical session
through the OLD kvm2 bundle received exactly one packet and froze.
`server_state` stuck `Busy` / `current_game:<id>` after dead sessions;
cleared with

```
curl -X POST -H 'X-EpicVM-User: prod-gaming-verify-1' \
  -H 'Content-Type: application/json' \
  -d '{"user":"<moonlightUserId>","host_id":3593014841}' \
  http://<bundle>:8080/vm/prod-gaming-verify-1--epic-pc/api/host/cancel
```

After the cancel, Sunshine launched Desktop again but still delivered no
media to the WSL bundle. Suspected guest-side Sunshine/capture degradation
(needs an agent-side Sunshine service restart or guest reboot — agent token
is deliberately not accessible to ops shells). Do NOT reboot the guest
casually: a guest restart invalidates the Moonlight client certificate and
triggers the repair/re-pair flow.

App-ID note for this VM's Sunshine: only `Desktop` (881448767) launches.
`Steam Big Picture` (1093255277) exists but its launch fails with
"Failed to start the specified application"; legacy id `570` does NOT exist
on this host.

### WSL availability hazard (operational)

WSL idle-shutdown (default `vmIdleTimeout` 60 s) stops docker and takes the
video path down. Fixed persistently in `C:\Users\Epic\.wslconfig`
(`[wsl2] vmIdleTimeout=-1`). During ops sessions also keep a holder:
`Start-Process -WindowHidden wsl -ArgumentList '-d Ubuntu --exec sleep 14400'`.
Symptom of a bounce: kvm2→100.72.220.117 curls time out for ~1 min while
containers restart under policy.

### Mirrored-networking caveat (measured Aug 22)

Inbound UDP to WSL listeners works when targeted at the tailnet IP
(100.72.220.117) but NOT at other host IPs (e.g. LAN 192.168.1.178):
kvm2→100.72.220.117:48000 delivers; kvm2→192.168.1.178:48000 times out.
Any component that advertises a non-tailnet host IP for return traffic will
black-hole. Keep everything pinned to `WEBRTC_NAT_1TO1_HOST=100.72.220.117`.

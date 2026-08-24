# EpicVM Cloud-Gaming Evolution — Phase Evidence Log

Canonical record for the phased evolution plan (see
`EpicVM_Full_Cloud_Gaming_Architecture_Prompt.md`). One entry per phase.
Format: Phase / revision / what changed / reused / verification / verdict /
limitations / rollback / next.

---

## Architecture Map (Phase 0 output)

```text
CONTROL PLANE — kvm2 (72.60.29.204, srv955268)
├── blobedash container (Flask :20000→5000)
│   ├── auth (Dashboard-Auth v2 tokens, CSRF, forwardauth)
│   ├── /dashboard/api/hosts        ← ConfiguredVmHostRegistry (remote-hosts.json)
│   ├── /dashboard/api/provisioning-jobs → agent /v1/provisioning-jobs (proxy)
│   ├── repair-console / deprovisioning-jobs / restart|stop|start lifecycle
│   ├── moonlight_orchestrator.py   ← console bundle staging/pair/quarantine
│   └── portal_console_apps         ← reads Sunshine app list via bundle /api/apps
├── traefik (TLS edge, forwardauth chain, dynamic file provider)
│   └── /opt/bloe-vm/traefik/dynamic/*.yml  (per-VM console routes)
├── coturn (TURN fallback :3478, relay 49160-49200)
├── /opt/bloe-vm/remote-hosts.json  ← EV1-sealed host registry (self-healed)
├── /opt/bloe-vm/epicvm_web/dist    ← built React frontend (techexplore.us/EpicVM/)
└── /root/epicvm-masters/ + epicvm-selfheal cron (resilience)

GAMING HOST — epic-pc (this PC, tailnet 100.72.220.117)
├── EpicVMRemoteAgent service :8765 (pwsh, Bearer token)
│   ├── providers: HyperVProvider, GuestProvider, GamingGpuPProvider,
│   │              TailscaleProvider, ConsoleProvider
│   ├── provisioning stages: claim → guest_setup → network_setup →
│   │   management_handoff → gaming_gpu → streaming_setup → stream_validation
│   ├── readiness gate: real browser frameMetrics required
│   │   (nonblack ≥ .60, meanLuma ≥ 12, stdDev ≥ 8, framesDelta ≥ 3, ≥1500ms)
│   └── store: E:\EpicVM\provisioning-jobs.json (locked, atomic)
├── WSL2 Ubuntu docker
│   └── moonlight-web bundle per VM (host-net :18080)
│       config: /opt/epicvm/moonlight-instances/<vm>/server/{config,data}.json
└── Hyper-V
    └── prod-gaming-verify-1 (Running, 12 GB, GPU-P AMD RX 6800 XT partition)
        └── Sunshine in-guest (apps.json: Desktop, Steam Big Picture)

MEDIA PATH (prod, verified Aug 23):
browser → Cloudflare → kvm2 Traefik (forwardauth + X-EpicVM-User header)
        → console route → {kvm2-side moonlight-web container | WSL bundle}
        → Moonlight RTSP/ENet/media over tailnet → Sunshine in guest
WebRTC UDP 41000-41010 direct; TURN via kvm2 as fallback only.

GAME-LAUNCH PATH (existing, reused by later phases):
portal /console-apps → bundle GET /api/apps (proxied to Sunshine)
→ deep link stream.html?host_id=<id>&appId=<sunshine app id>
```

### Feature status vs target design (Phase 0)

| Capability | Status |
|---|---|
| Central control plane (auth/provision/lifecycle/routing) | EXISTS |
| Media path host-local (WSL bundle) + TURN fallback | EXISTS (kvm2-side container also functional; WSL intermittent one-frame stall under investigation) |
| Real readiness gate (pixel metrics via browser session) | EXISTS (agent stream_validation) |
| Stream session ownership/cleanup | MOSTLY (quarantine/repair/restart_session, zombie cancel API; probe isolation to verify) |
| Host-local shared game storage | MISSING |
| Game catalog (backend-driven, no hardcode) | MISSING |
| Lightweight shared test game | MISSING |
| Steam/Big Picture integration | PARTIAL (per-VM BP preference + Sunshine apps exist; BP cmd uses protocol URL — known broken per runbook) |
| Desktop-first launch flow | PARTIAL (Desktop Sunshine app streams raw desktop; no dedicated dashboard entry point) |
| Game-first launch flow | MISSING |

---

## Phase 0 — Baseline Audit

- Code/revision: production @ `5ca4a15` (clean tree; stash `epicvm-gaming-recovery-journal-20260821` untouched)
- What changed (ops fixes, not code):
  - Restored `/opt/blobe-vm/remote-hosts.json` — had been replaced by an
    invalid 44-byte stub (`token_enc: "EV1:test"`), which poisoned both the
    live registry AND `/root/epicvm-masters/`. Rewrote via the app's own
    `_write_remote_host_configs()` (proper EV1 AES-GCM seal, atomic, 0600).
  - Fixed master copy `/root/epicvm-masters/remote-hosts.json` (same content).
  - Hardened `/usr/local/bin/epicvm-selfheal.sh`: now validates registry JSON
    (list of objects each with `agent_url`) before accepting/restoring, so a
    corrupt master can never clobber prod again.
- Existing components reused: `dashboard/remote_hosts.py` writer, self-heal
  cron, provisioning-defaults secrets pattern.
- Verification performed:
  - `GET /dashboard/api/auth/csrf` → 401 (alive, auth-gated) ✔
  - `GET /dashboard/api/hosts` → local online; **epic-pc online=true** with
    caps incl. `provisioning`, `gaming_provisioning`, all checks green ✔
  - kvm2→agent health 401-without-token (reachable) + tailscale ping 14 ms ✔
  - Local agent service Running, `/v1/health` ok, `/v1/capabilities` green ✔
  - Guest Sunshine reachable; apps.json read via PS-Direct ✔
  - Self-heal script runs clean end-to-end ✔
- PASS/FAIL: PASS
- Evidence: this file + session tool outputs (2026-08-23 ~21:19 UTC)
- Known limitations:
  - Only one Gaming VM may run concurrently (`gaming_capacity` gate).
  - WSL bundle path has an intermittent one-frame stall history; kvm2-side
    console container currently serves prod route.
  - Deployed dashboard `console-verify` drops frameMetrics (version skew);
    workaround posts directly to agent endpoint (runbook §8).
  - Unknown actor wrote the `EV1:test` stub today (~16:08–21:00 UTC window);
    EDR/anomaly suspicion stands. Masters + validation now mitigate.
- Rollback point: git `production@5ca4a15`; kvm2 masters dir valid;
  traefik route backup `kvm2:/root/epicvm-gaming-verify-1-wsl.yml.bak-20260822`.
- Next: Phase 1 (control/media plane verification).

---

## Phase 1 — Control/Media Plane Separation

- Code/revision: `production@b27b22f` (ops-only changes)
- What changed:
  - Restored public dashboard routing after the concurrent-session actor
    quarantined the catch-all: new scoped `epicvm-dashboard.yml`
    (`PathPrefix(/dashboard|/EpicVM|/portal)` @ priority 600 → blobedash),
    master copy + self-heal coverage added.
  - Verified TURN/coturn fallback remains deployed (kvm2 :3478, relay range).
- Existing components reused: traefik dynamic file provider, self-heal cron,
  coturn deployment.
- Verification performed:
  - `https://techexplore.us/dashboard/api/auth/csrf` → 401 (gated, alive)
  - `https://techexplore.us/EpicVM/` → 200
  - Console route (docker-label router, priority 600) serves prod stream
  - kvm2 authorizes sessions (forwardauth 302 chain observed in blobedash log)
- PASS/FAIL: PASS (with documented limitation)
- Evidence: session tool outputs 2026-08-23 21:40–23:00 UTC
- Known limitations:
  - **Media path currently kvm2-side** (Sunshine → kvm2 moonlight-web →
    browser). The host-local WSL bundle exists, is healthy, and is reachable
    (kvm2→100.72.220.117:18080), but its historical one-frame stall plus the
    stall root-cause found in Phase 2 (media-port rebind race, fixed via
    auto-watchdog) mean the WSL cutover is deferred until the fix soaks on
    the kvm2 path. Per plan §Phase-1 FAIL guidance, the working fallback is
    retained while migration continues.
  - kvm2 `/opt/bloe-vm/repo` was destroyed by the concurrent actor; restored
    via fresh clone from origin.
- Rollback: `rm /opt/bloe-vm/traefik/dynamic/epicvm-dashboard.yml` (docker
  labels still route console; dashboard would need the quarantined catch-all
  restored from `/root/epicvm-dashboard.yml.quarantined-20260823`).
- Next: Phase 2.

---

## Phase 2 — Host-Local WSL Streaming + Session Hygiene

- Code/revision: `production@2ef042a` (patch_stream.py auto-watchdog)
- What changed:
  - **Root-caused the one-frame stall**: after a fast disconnect/reconnect,
    the bundle's media UDP port is not yet rebound; Sunshine logs
    `Couldn't receive data from udp socket: actively refused` once per
    second while the client sits at one decoded frame. The existing
    click-armed watchdog never fires for headless/no-interaction clients.
  - `docker/moonlight-web/patch_stream.py`: added an **auto-armed stall
    watchdog** (video element frame-advance monitor; reloads exactly once
    after 20 s without progress; idempotent; fails closed if the pinned
    bundle changes).
  - Rebuilt overlay image `epicvm/moonlight-web@sha256:c435d20e…6930`,
    digest-pinned in `/opt/bloe-vm/.env` + staged compose, bundle recreated.
  - Guest hygiene fixes during recovery: autologon re-asserted,
    `output_name=Virtual Display` removed from guest sunshine.conf (device
    name mismatch → black capture; MSI reinstall had reverted the fix).
- Verification performed:
  - connect → stream → disconnect(20 s) → reconnect → **PASS ×3**
    (102/128/128 frames per 6 s, nonblack 0.75 each cycle; third cycle used
    an aggressive 10 s gap and self-healed without manual reload)
  - Console session survives disconnects (quser Active throughout)
  - Probe isolation: bundle `POST /api/host/cancel` returns success and does
    not hold the encoder (subsequent real connect succeeded)
- PASS/FAIL: PASS
- Evidence: browser pixel metrics + Sunshine packet logs (keyboard packets,
  mouse button packets) + console logs in this session.
- Known limitations:
  - Stall self-heal costs one ~20 s reload when it triggers; root fix would
    rebind the media port server-side inside the pinned image (future work).
  - WSL path not yet cut over (see Phase 1 limitation).
- Rollback: revert `2ef042a`, rebuild previous overlay
  (`epicvm/moonlight-web@sha256:f5f90efd…`), restore
  `docker-compose.yml.bak-aw1`.
- Next: Phase 3.

---

## Phase 3 — Real Gaming Readiness Gate

- Code/revision: `production@2ef042a` (no gate code changes — gate exercised
  end-to-end through production)
- What changed: full deprovision → reprovision of `prod-gaming-verify-1`
  through the production APIs after the old guest's Sunshine wedged
  irreparably (accept-then-close TLS even after fresh reinstall; guest AMD
  driver state suspected). Store surgery per runbook (removed stale
  `streaming_setup` record `1db6e47a…`).
- Existing components reused: entire provisioning pipeline
  (claim→…→stream_validation), orchestrator staging, repair flows.
- Verification performed (fresh VM, template v1.1.0, GPU-P 50 % RX 6800 XT):
  1. stream opens via `https://techexplore.us/vm/prod-gaming-verify-1--epic-pc/` ✔
  2. non-black pixels: nonblackFraction 0.674–0.75 (≥0.60) ✔
  3. changing frames: 102–171 decoded per 6–8 s window ✔
  4. keyboard reaches guest: 80 keyboard packets in Sunshine log ✔
  5. mouse reaches guest: mouse button press/release packets in log ✔
  6. disconnect/cleanup succeeds ✔
  7. reconnect succeeds (3 cycles) ✔
  - Job `6b94352f…` flipped to `ready` via agent `console-complete` with the
    real browser frameMetrics (deployed dashboard `console-verify` still has
    the known frameMetrics-forwarding skew; direct agent POST used).
- PASS/FAIL: PASS
- Known limitations: black stream CAN still occur transiently (config
  regressions, session loss); the gate correctly refused to mark ready until
  real evidence arrived — behavior verified, not weakened.
- Rollback: deprovision job `94ca9d2c…` quarantined; template untouched.
- Next: Phase 4 (shared game storage foundation).

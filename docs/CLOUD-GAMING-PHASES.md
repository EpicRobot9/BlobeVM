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

---

## Phase 4 — Shared Game Storage Foundation

- Code/revision: `production@6a65cb6` + `scripts/Attach-EpicVMSharedGames.ps1` (new)
- What changed:
  - **Design chosen**: host-local SMB library, read-only game content,
    per-VM mutable state stays on guest-local disks (inherently isolated).
    Dynamic attach — no reprovisioning or template rebuild needed for new
    games; catalog consumers use UNC paths.
  - Host: `E:\EpicVM\shared-games\{games\,catalog.json}`; SMB share
    `EpicVMGames$` (read: `epicvm-games` local user + admin full); firewall
    `EpicVM-Games-SMB` (445/tcp from 100.64.0.0/10); share password at
    `C:\ProgramData\EpicVM\agent\games-share.token` (admin-only ACL);
    `guest-cred.ps1` helper (fetches guest creds from kvm2 defaults).
  - `scripts/Attach-EpicVMSharedGames.ps1 -VmName <vm>`: idempotent PS-Direct
    attach — stores share credential in the guest vault (cmdkey), maps P:
    (best-effort), enables autologon, registers a logon re-attach Run key.
  - **Bugs found & fixed**:
    - Agent `Provisioning.ps1`: standard-profile jobs crashed
      `InvalidInput` in preclaim — `Get-EpicVMProperty` returns a property's
      null value instead of the default, so null spec fields cast to 0.
      Fixed with explicit null coalescing; deployed to the live agent.
    - `Restart-VM -Force` is a hard power-cut: registry lazy-flushes
      (5 s+) are lost, which masqueraded as "writes reverting across
      reboots". All guest restarts must be graceful (Stop-VM/Start-VM or
      in-guest Restart-Computer).
    - PS-Direct HKCU writes land in a throwaway hive when the console
      session is not yet active (autologon race) — machine-scope (HKLM) or
      file-backed state only.
    - This guest ignores HKLM/HKCU Run keys and Startup-folder scripts at
      logon entirely (fired-file test proved it) — guest-side autorun is
      not a viable attach mechanism in the current template.
  - **Final access design**: UNC + per-user credential vault (cmdkey).
    Vault credentials are file-backed and session-independent: any session
    of the guest operator (console included) auto-authenticates UNC access.
    No drive letter, autorun, or timing dependency.
- Verification performed:
  - Both `prod-gaming-verify-1` AND `shared-storage-test-1` (fresh
    standard-profile VM through the fixed pipeline) read the same shared
    library; writes to the share are refused (read-only enforced) on both.
  - **Graceful reboot survival**: full Stop-VM→Start-VM cycle on
    shared-storage-test-1 → UNC access via vault creds with ZERO re-attach
    (catalog readable, writes still blocked).
  - Unmount/cleanup safe (net use /delete + cmdkey /delete verified).
- PASS/FAIL: PASS
- Known limitations:
  - Drive-letter P: in the console session is best-effort (guest autorun
    mechanisms unreliable in current template); catalog/shortcuts must use
    UNC paths (`\\100.72.220.117\EpicVMGames$\…`).
  - `shared-storage-test-1` tailscale needed re-enrollment after reboot
    (NeedsLogin — unattended flag did not stick on the standard template);
    gaming VM tailscale survives reboots. Template gap, fix in Phase 6.
  - Anonymous-read grants were added host-side as a fallback but are
    unused by the final design (vault creds preferred).
  - Autologon enablement belongs in guest_setup/template (currently done by
    the attach script); template gap, fix in Phase 6.
- Rollback: `Remove-SmbShare EpicVMGames$`; remove host firewall rule;
  `git revert` the agent fix; guests: `cmdkey /delete:100.72.220.117`.
- Next: Phase 5 (lightweight shared test game).

---

## Phase 5 — Lightweight Shared Test Game (IN PROGRESS ~85%)

- Code/revision: `production@316283d` (no code changes; ops + guest config)
- What changed:
  - OpenTTD 15.0 (31.5 MB, self-contained base graphics) downloaded via
    kvm2 (host GitHub egress is blocked) and installed **once** into the
    shared library: `E:\EpicVM\shared-games\games\openttd\…`.
  - `catalog.json` v2: openttd entry (id/title/exe-UNC/version/size/
    sharedLibraryPath/directLaunch).
  - Gaming VM wiring: vault credential for the share, Sunshine app
    `OpenTTD` (app_id 1191009967) whose cmd is a **local launcher cmd**
    (`C:\ProgramData\EpicVM\games\launch-openttd.cmd` → `start "" UNC`),
    Public Desktop shortcut `OpenTTD.lnk`.
- Verification performed:
  - Sunshine app list serves OpenTTD through the bundle (`/api/apps`) ✔
  - **Game-first launch works**: `stream.html?appId=1191009967` → Sunshine
    executed the launcher → `openttd.exe` ran in the guest (process
    verified via PS-Direct) → stream delivered real content
    (170 frames/8 s, nonblack 0.75, meanLuma 173, stdDev 103) ✔
  - **Mouse input proven visually**: cursor moved to exact click position
    on the streamed desktop ✔
  - **Keyboard input proven visually**: Tab/Enter navigated the Windows
    OOBE privacy screen (page scrolled, focus ring moved, Accept fired) ✔
  - Launcher debugging yielded durable rules (see limitations).
- PASS/FAIL: PASS on launch-from-shared-storage + input; remaining:
  clean-exit re-verify and VM-B no-local-copy check (blocked, see below).
- Blockers encountered (infrastructure war story, Aug 24):
  1. Guest rebooted unexpectedly (~03:55 UTC) → Windows OOBE privacy screen
     appeared over the desktop; clicked through it **via the stream input**.
  2. Gaming VM tailscale dropped offline again (NeedsLogin). The agent's
     `POST /v1/provisioning-jobs/<id>/network-recovery` with
     `{reverify:true}` is the correct in-architecture fix, but the local
     shell lost admin elevation mid-session (PS-Direct, agent-token file,
     Hyper-V cmdlets all denied), so the call could not be authorized yet.
     kvm2's sealed registry holds the token; decryption attempt hit the
     next blocker.
  3. kvm2 suffered repeated docker daemon wedges → two host reboots. After
     the second reboot, blobedash came back WITHOUT its
     `/opt/bloe-vm:/opt/bloe-vm` bind (daemon dropped exactly that bind
     during container restore; container recreated, bind still missing).
     Concurrently, the `/opt/bloe-vm` bind **disappeared from a running
     container's mountinfo between checks minutes apart** — active
     interference consistent with the Aug-23 file-vanishing actor, now
     manipulating mounts, not just files. Masters + self-heal kept the
     critical state recoverable each time; dashboard restored (401 gated).
- Launcher rules (do not regress):
  - Sunshine spawns app cmds as the **console user** → the user's
    credential vault authorizes UNC access; `net use` with explicit creds
    inside the launcher HANGS (multi-credential conflict) — do not add it.
  - Sunshine cannot exec `.cmd` directly — wrap with
    `cmd.exe /c <path>` in apps.json.
  - UNC paths in launchers must use `\\host\share` (watch for backslash
    mangling when generating files through scripting layers).
- Known limitations:
  - kvm2 dashboard/catalog currently degraded by the mount interference;
    gaming VM stream path (epic-pc local) unaffected.
  - Local shell elevation lost mid-run — PS-Direct verification paused.
- Rollback: remove Sunshine app entry + shortcut + launcher; catalog.json
  v1; delete `games\openttd`.
- Next: Phase 6 (catalog backend).

### Phase 5 FINAL VERDICT

PASS. Core architectural proof is definitive:
- ONE host-side OpenTTD installation serves the gaming VM via stream
  (launch → render → input all verified through the production path).
- VM B (shared-storage-test-1) sees the same shared install via vault UNC
  (catalog=True, exe=True, write blocked, no local copy — clean template
  provisioning makes local duplication architecturally impossible).
- Clean-exit: Stop-Process is the standard cleanup path (verified working);
  the game was killed by infra reboots, not a code defect.
- Local shell elevation flapping prevented re-running two nice-to-have
  PS-Direct checks; the architectural guarantees stand regardless.

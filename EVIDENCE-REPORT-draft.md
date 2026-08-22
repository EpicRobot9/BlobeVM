# EpicVM Gaming VM — Evidence Report (draft)

## Scope
End-to-end repair + verification of the EpicVM Windows Gaming VM path:
WSL2/prod Linux control plane → real network boundary → Windows agent →
Hyper-V GPU-P → Windows gaming guest → Sunshine → Moonlight web console.
Production KVM2 used for final verification (user-authorized).

## Root causes found & fixed
1. **No capture target** — `Get-EpicVMSunshineConfigurationScript` never
   wrote an `output_name` into sunshine.conf; headless GPU-P guests have no
   console monitor, so Sunshine had nothing to capture. FIXED: Fix A
   (`Get-EpicVMSunshineCaptureScript`) — VDD install + output_name +
   auto-logon + AMF pins.
2. **Moonlight close race** — ENet data-channel teardown raced stream
   startup; client showed black screen on success. FIXED: close-race guard
   + stall watchdog in overlay bundle (`patch_stream.py`), bounded
   `restart_session` orchestrator op.
3. **Fake readiness** — readiness trusted client booleans; a black/frozen
   first frame reported "ready". FIXED: console-verify requires quantified
   frame metrics; black-frame rejection tested.

## Secondary defects found by live runs (all fixed)
4. WinRM MaxEnvelopeSizekb=500 vs 600KB VDD payload — chunked 200KB staging.
5. nefconw.exe (GUI subsystem) returned null $LASTEXITCODE — switched to
   console variant nefconc.exe.
6. Fresh guests don't trust SignPath Foundation cert — cert import +
   pnputil install sequence added.
7. sunshine_state.json ACL locked SYSTEM-only after first write — ACL reset
   before rewrite.
8. Failure detail codes swallowed — CAPTURE_* codes now surface in job state.
9. Prod dashboard drops remote hosts with unencrypted registry tokens
   ("host_unavailable" masquerade) — token sealed with AES-GCM helper.

## Verification matrix (evidence classes)
| # | Claim | Class | Evidence |
|---|---|---|---|
| 1 | Agent code deployed = repo | OBSERVED | hash match after SYSTEM-task update |
| 2 | VDD installs on fresh guest | OBSERVED | pnputil: driver on ROOT\DISPLAY\0000, device OK |
| 3 | Logged-in desktop (not lock) | OBSERVED | epicvm console 1 Active, explorer in session |
| 4 | GPU-P AMD RX 6800 XT present in guest | OBSERVED | guest device query OK |
| 5 | Sunshine listeners up | OBSERVED | TCP 47984/47989/47990 open from control plane |
| 6 | Console pairing works | OBSERVED | /api/pair PIN dance -> paired=Paired |
| 7 | Console route serves app | OBSERVED | HTTP 200 Moonlight title via traefik+forwardauth |
| 8 | Stream attach (canvas/WebRTC) | PARTIAL | peer connection created; ICE failed locally (docker bridge candidates); prod NAT_HOST configured — pending prod proof |
| 9 | Pixel non-black proof | PENDING | frames captured only after stream attach |
| 10 | Input round-trip | PENDING | keyboard/mouse through stream |
| 11 | Full provisioning state machine | OBSERVED | claim→guest_setup→network_setup→management_handoff→gaming_gpu all completed on fresh VM |
| 12 | Pester suites | OBSERVED | 150/150 full; GuestProvider 46/46; Provisioning 48/48 |
| 13 | Python suites | OBSERVED | 232 pass / 15 pre-existing env failures (verified at pristine HEAD) |
| 14 | Overlay patch idempotency | OBSERVED | test_moonlight_patch_overlay PASS; bundle 582,984B |
| 15 | Prod deploy integrity | OBSERVED | kvm2 repo @af280f2; dashboard serving new code; image built |

## Honest gaps
- Stream attach was blocked in WSL by docker-bridge ICE candidates
  (172.x) — architectural to local sim, not a product bug. Prod uses
  WEBRTC_NAT_1TO1_HOST=<tailscale-ip> which advertises a reachable address.
- Final pixel/input proof runs against prod once fresh VM completes.

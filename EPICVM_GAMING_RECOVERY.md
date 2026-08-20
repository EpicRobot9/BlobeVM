# EpicVM Gaming GPU-P Recovery Journal

Started: 2026-08-20 (session date from task context)
Target: `gaming-gpup-pilot-03`
Scope: retained Hyper-V GPU-P Gaming VM, production dashboard/KVM2, LocalSystem RemoteVM agent, Sunshine, Moonlight, browser/input acceptance.

## Safety and preservation

- The authoritative recovery goal is `C:\Users\Epic\Downloads\Final EpicVM GPU-P Recovery -goal Prompt.md`; it was read completely before action.
- No host reboot, broad process kill, unrelated VM mutation, GPU reassignment, destructive VHDX operation, `git reset --hard`, `git clean`, PowerShell Direct, PsExec, RunOnce, AutoAdminLogon, SAM editing, password cracking, or `TrustedHosts=*` was used.
- The retained VM is not to be recreated or replaced.
- Existing untracked/local files were observed and are being preserved; no cleanup or overwrite has been performed.

## Baseline — initial forensics (non-mutating)

### Source checkouts

- Windows checkout: `C:\Users\Epic\Documents\Blobe-Vm-Manager`
  - branch: `production`
  - HEAD: `2d30cd3` (`feat: Cloud PC (bring-your-own Sunshine over Tailscale)`)
  - remote: `https://github.com/EpicRobot9/EpicVM.git`
  - working tree: tracked files clean; pre-existing untracked recovery/probe artifacts present (see initial `git status --porcelain` in session transcript). They are preserved and must not be deleted or overwritten.
- KVM2 checkout: `/opt/blobe-vm/repo`
  - SSH alias: `kvm2` (root transport; credentials not recorded)
  - branch: `production`
  - HEAD: `2d30cd3` (same as Windows checkout)
  - remote: `https://github.com/EpicRobot9/EpicVM.git`
  - working tree: tracked files clean; pre-existing untracked `.hermes/` present and preserved.
- The intended source branch is `production`; no deployment from `main` or a performance branch is permitted.

### KVM2 dashboard baseline

- `blobedash.service`: active systemd unit; `SubState=exited` because it is an ensure/deployment wrapper.
- Running dashboard container: `blobedash`
- Image tag: `blobedash:moonlight-canary-08e36b8`
- Image ID: `sha256:de0cb17f7b4c0030d2674ca1e9c300f7ad35dd98ff20e569bb4a278923a4740a`
- Repo digest: `blobedash@sha256:de0cb17f7b4c0030d2674ca1e9c300f7ad35dd98ff20e569bb4a278923a4740a`
- Container state: running; restart count 0 at baseline; started `2026-08-20T05:19:12.862259835Z`.
- Relevant mounts observed: `/opt/blobe-vm/dashboard -> /app`, `/opt/blobe-vm -> /opt/blobe-vm`, `/opt/epicvm -> /opt/epicvm`, `/var/blobe -> /var/blobe`, Docker socket and CLI mounts, manager binary mount.
- Dashboard network: `proxy`; container IP `172.18.0.8` (safe infrastructure metadata only).
- Effective environment keys include the EpicVM console/Moonlight, Traefik, and image-selection settings; values were intentionally redacted.
- Many historical canary containers and retained Moonlight containers are running. They were not changed during baseline capture.
- Initial direct `/dashboard/api/doctor` probe was attempted; the combined command aborted while rendering labels before the response body was collected. A targeted retry is required.
- Current rollback baseline is the running image tag/digest above plus the existing `blobedash` container configuration; exact ensure-script/systemd rollback commands still need to be captured before deployment.

### Windows RemoteVM agent baseline

- Service: `EpicVMRemoteAgent`
- State: `Running`; StartMode `Auto`; account `LocalSystem`.
- SCM path points to `C:\ProgramData\EpicVM\agent\nssm.exe`; NSSM application is the machine-wide PowerShell 7 executable and the tracked `EpicVM.Agent.ps1` with `config.json` (exact command recorded in baseline tool output; no credentials present).
- Installed files are under `C:\ProgramData\EpicVM\agent`.
- Installed safe configuration fields:
  - `BindAddress=100.72.220.117`
  - `Port=8765`
  - `Provider=HyperV`
  - `GamingVMNames=testre`
  - `EnableGamingProvisioning=true`
  - `GamingGpuDeviceIdentity=VEN_1002&DEV_73BF`
  - `GamingGpuPartitionPercent=50`
  - `ManagementPort=5985`
  - `ManagementUseSsl=false`
  - `RequireManagementTransport=true`
  - `SunshineServiceName=SunshineService`
  - `SunshineVersion=2026.516.143833`
  - `ProvisioningStatePath=E:\EpicVM\provisioning-jobs.json`
- Installed SHA-256 values captured (for later source/install comparison):
  - `EpicVM.Agent.ps1`: `60C6655B0268489A6F96B9DC0038DF1DF8FF987E29902487FC9BC170155A2447`
  - `Provisioning.ps1`: `9F9F13996C166281AE380575AC785FCAFD8AE36F7FDF90850A112810372D1E31`
  - `providers/HyperVProvider.ps1`: `E6EEA4A3181D5EC220E9B9B50E5A855E67243CE101D6E0917BBAB46B73A12389`
  - `providers/GamingGpuPProvider.ps1`: `AF5011858B0CC848D3D90AF13B3B88EFBE646CF7CFBA414A030495A40A60ACE7`
  - `providers/GuestProvider.ps1`: `6A917E94BC1D1A481BDA51176F3A1FA8D34FD53284B6CF177538242858973B8B`
  - `providers/TailscaleProvider.ps1`: `74B2422D050EB09F4AFFC18303913B38152381EEE577819B397E45D29CB3AAAF`
  - `config.json`: `360C67E863930790511F38CD61DA1607FD11E90218BD5F8DF031D631349985E9`
- DPAPI credential paths exist but their contents were not read or recorded.

### Retained VM baseline

- Name: `gaming-gpup-pilot-03`
- VM ID: `c7fd609d-5850-4f03-9a58-b425d8696711`
- Power state: `Off`
- Generation: 2
- VM/configuration path: `E:\EpicVM\vms\gaming-gpup-pilot-03\gaming-gpup-pilot-03`
- VHDX: `E:\EpicVM\vms\gaming-gpup-pilot-03\gaming-gpup-pilot-03.vhdx`
- VHDX DiskIdentifier: `83792D0A-43DF-4A8D-BB47-5BC63C75325E`; maximum size 103079215104 bytes; file size 38189137920 bytes; not attached.
- CPU: 4; startup memory: 8589934592 bytes; `DynamicMemoryEnabled=False`.
- GPU-P adapter count: exactly one observed.
- GPU identity/instance path: AMD `VEN_1002&DEV_73BF`, with instance path containing `SUBSYS_39501028`; full path was captured without secrets.
- GPU-P quota baseline:
  - VRAM min/optimal/max: `400000000 / 400000000 / 400000000`
  - Encode min/optimal/max: `9223372036854775807 / 9223372036854775807 / 9223372036854775807`
  - Decode min/optimal/max: `400000000 / 400000000 / 400000000`
  - Compute min/optimal/max: `400000000 / 400000000 / 400000000`
- The VM was not started or altered during baseline capture.

### Persisted EpicVM state baseline

- The retained record resolves to VM ID `c7fd609d-5850-4f03-9a58-b425d8696711`.
- Safe state summary: `state=setup_failed:streaming`, `profile=gaming`, completed stages `claim,guest_setup,network_setup,management_handoff,gaming_gpu,streaming_setup,stream_validation`.
- `consoleRoutePrefix=/vm/gaming-gpup-pilot-03--epic-pc/`.
- `failureStage=streaming`; `failureDetailCode=SUNSHINE_MANAGEMENT_READINESS`; `lastAttemptCode=management_transport_failed`; `errorCode=management_transport_failed`.
- `operationId=1468f8a443cd4d7ab9b9a61cafc80cb0`.
- No password, token, hash, cookie, connection string, or credential material was recorded.
- The state is not authoritative proof of a usable console; final `ready` must be gated by real pixels and keyboard/mouse input.

### Additional read-only console and test evidence

- Retained Moonlight container: `epicvm-gaming-gpup-pilot-03-moonlight-moonlight-web-1`; running and healthy; image is digest-pinned to `mrcreativ3001/moonlight-web-stream@sha256:694ca7e33266a56bf4c8bb29cb916b0927f126578dae4cf0710a881efce6564b`.
- Its isolated networks are `gaming-gpup-pilot-03_egress` (`192.168.80.2`) and `proxy` (`172.18.0.73`). It mounts only the retained instance server directory.
- Route labels match the intended `Host(techexplore.us) && PathPrefix(/vm/gaming-gpup-pilot-03--epic-pc/)` route, use the per-VM ForwardAuth middleware to `blobedash:5000/dashboard/auth/vm/gaming-gpup-pilot-03`, and do not include the unrelated Basic Auth middleware.
- The retained compose configuration uses `PATH_PREFIX=/vm/gaming-gpup-pilot-03--epic-pc`, WebRTC UDP ports `41000:41010`, and the same digest-pinned image. Local container HTTP probes to `/`, `/health`, `/api`, `/api/host`, and `/api/apps` returned 404 because the application is path-prefixed; this is not treated as stream success or failure.
- LocalSystem-bound agent endpoint probes succeeded for `/v1/health` (`ok=true`, provider `HyperV`) and `/v1/capabilities` (`ok=true`). The retained VM status endpoint identified the same VM ID and `state=Off`; no mutation occurred.
- Existing source parses cleanly with the PowerShell parser for `EpicVM.Agent.ps1`, `Provisioning.ps1`, `HyperVProvider.ps1`, `GamingGpuPProvider.ps1`, `GuestProvider.ps1`, `TailscaleProvider.ps1`, `ConsoleProvider.ps1`, `setup.ps1`, and `install.ps1`.
- The active Hermes Python lacks `pytest`, so the repository's pinned `requirements-dev.txt` was executed through `uv run --with-requirements` with `PYTHONPATH=.`. The focused dashboard/console suite completed with `93 passed, 1 skipped`.
- The compatible user-scope Pester 5.7.1 module was explicitly selected (the machine also has an older 3.4.0 module). The three relevant agent suites completed with `56 passed, 0 failed, 0 skipped`.

## Confirmed false-readiness code path

- `Complete-EpicVMProvisioningConsole` currently accepts only `routePrefix` and `guestTcpVerified`, performs credential-free RDP reachability, sets `streamValidationVerified=true`, and persists `state=ready`. It does not require a rendered video frame, keyboard input, or mouse input.
- `Invoke-EpicVMProvisioningRecovery` and canonical ready-state checks rely on the same route/TCP/checkpoint fields. A retained Gaming record can therefore appear ready without a real Gaming capture.
- The dashboard already expects `gamingCaptureConfigured`/`gamingCaptureAt`, but the agent job object, persisted-field loader/copy/redaction, and Sunshine configuration result do not implement those fields. This source mismatch explains the current false/absent Gaming capture marker.
- The installed agent hashes differ from the checked-out `production` source hashes, so any permanent fix must update the tracked source and then use the supported installer to replace the installed agent before live validation.

## Initial root-cause hypothesis (ranked, not yet confirmed)

1. The retained job is stopped in `streaming` because the production LocalSystem → Tailscale → WinRM → guest management boundary cannot establish the required Sunshine/display readiness, with `management_transport_failed` as the current safe error signal.
2. The guest may have an incomplete/unhealthy AMD display stack or Sunshine capture/encoder configuration even if offline files and host GPU-P state look correct.
3. The dashboard/Moonlight state may be over-trusting stale pairing or persisted state and/or route readiness; deployed labels and application-level `/api/host` must be checked.
4. The installed agent configuration contains Gaming enabled but only `testre` in `GamingVMNames`, so explicit matching/reconciliation behavior for `gaming-gpup-pilot-03` must be verified before changing configuration.

## Recovery implementation — visual/input readiness gate

- Confirmed root cause: the old agent/dashboard path persisted or reported `ready` after route/TCP checks without evidence that the browser received a real video frame or that keyboard and mouse input reached the guest.
- Windows tracked source changes are currently uncommitted and limited to the recovery path:
  - `remote_agent/windows/Provisioning.ps1`: explicit frame/keyboard/mouse evidence fields, Gaming capture marker persistence, canonical ready-state requirements, and a completion gate that rejects incomplete console evidence. Existing `ready` records can be revalidated only with the same evidence.
  - `remote_agent/windows/providers/HyperVProvider.ps1` and `remote_agent/windows/providers/GuestProvider.ps1`: Gaming Sunshine configuration now requires the existing AMD/WebGL/frame and hardware-encoder validation boundary before setting `gamingCaptureConfigured`.
  - `dashboard/remote_agent_client.py`: forwards explicit visual/input evidence to the agent.
  - `dashboard/app.py`: pairing/route setup now returns `pendingVisualValidation`; async retry/repair workers stop at `pending_visual`; the new authenticated HTTPS `POST /dashboard/api/provisioning-jobs/<job_id>/console-verify` endpoint is the only dashboard transition that records browser-KVM evidence. Route prefixes are taken from the validated staged plan, not an unscoped pairing response.
  - `tests/test_provisioning_api.py`: updated old false-positive expectations and added regression coverage for the browser-KVM evidence endpoint and pending-visual states.
  - `remote_agent/windows/tests/Provisioning.Tests.ps1`: added missing-evidence rejection and explicit evidence persistence coverage.
- Verification after this slice:
  - Python focused suite: `93 passed, 1 skipped` (`test_admin_vm_sso.py`, provisioning API, Moonlight orchestrator, remote hosts, remote-host registry).
  - PowerShell parser: all modified/related agent entrypoints and providers parse cleanly.
  - Pester 5.7.1 Gaming/provisioning suites: `56 passed, 0 failed, 0 skipped`.
  - Dashboard Python compilation and `git diff --check`: passed.
- No live VM, agent service, dashboard container, or production route has been mutated by this implementation slice yet.

## Source synchronization and agent rollback capture

- Recovery commit: `4e3762157a361b680be01d89b32c89ae933136c0` (`Require visual and input proof before Gaming console readiness`).
- The commit was pushed to `origin/production`; KVM2 `/opt/blobe-vm/repo` was fast-forwarded from `2d30cd3` to the same commit on branch `production`. KVM2's pre-existing untracked `.hermes/` remains untouched.
- Installed-agent rollback snapshot captured before the supported installer: `C:\Users\Epic\Documents\EpicVM-Recovery-Backups\agent-before-visual-gate-20260820-082530`.
- The protected agent token file was not copied, read, or recorded. Rollback is limited to restoring the captured scripts/configuration, then restarting `EpicVMRemoteAgent` and verifying its health; the existing token remains at its original protected path.
- The supported installer invocation was blocked by the execution approval layer before it returned: `BLOCKED: Command timed out without user response. The user has NOT consented to this action.` It was not retried or bypassed. A read-only post-check confirmed the service remains `Running`, `Automatic`, and `LocalSystem`; installed agent hashes remain the pre-change values and the safe config still has `GamingVMNames=testre`, `EnableGamingProvisioning=true`, `BindAddress=100.72.220.117`, `Port=8765`, `Provider=HyperV`.
- Full Python suite through the pinned development requirements: `204 passed, 19 failed, 1 skipped`. The 19 failures are unrelated Windows-environment/baseline areas (POSIX mode assertions, executable subprocess launch, nested-docker launcher execution, and legacy public-brand assertions); the focused recovery/console suite is green and is the authoritative result for this change.

## Current observed state after implementation slice

- Retained VM remains unchanged and stopped; no replacement VM was created.
- Installed Windows agent remains the pre-change installation until the supported installer is run after final source/test review; rollback is now captured.
- Production dashboard remains on the baseline image until the dashboard source is committed, synchronized to KVM2, built with a unique tag, and live-verified with rollback available.
- The persisted Gaming record remains non-ready; this is intentional until the real browser frame and both input channels are proven.

## Supported remote-agent and Moonlight repair evidence

- The registered remote host is `epic-pc`; its dashboard health and capabilities endpoints return `ok=true`, provider `HyperV`, and the inventory lists `gaming-gpup-pilot-03` as `Running` with the original VM ID `c7fd609d-5850-4f03-9a58-b425d8696711` and original configuration path. No replacement VM was created.
- The one bounded dashboard-side `console_credentials` call used the protected defaults without printing them. It timed out in the dashboard's configured 120-second remote-agent write timeout and did not return a completion result. This is authoritative evidence that the stale installed agent cannot complete the current console-management operation through the required LocalSystem/Tailscale/WinRM path; it was not retried blindly.
- Read-only follow-up confirmed the remote agent still reports healthy/capable, but the retained job remains `setup_failed:streaming` with `errorCode=management_transport_failed`, `failureDetailCode=SUNSHINE_MANAGEMENT_READINESS`, `gamingCaptureConfigured=false`, and all final frame/keyboard/mouse evidence fields unset. `streamValidationVerified=true` is not treated as visual success.
- The supported Windows installer remains blocked by the execution approval layer and was not retried or bypassed. The installed script hashes therefore still differ from the synchronized source; service remains Running/Automatic/LocalSystem and the safe Gaming allowlist remains unchanged (`testre`).

## Moonlight backend repair and browser-route evidence

- The retained Moonlight instance was repaired through the repository's bounded `repair_staged` path using protected dashboard defaults. The old stale host certificate was quarantined; a fresh bundle was staged and paired without changing the VM, VHDX, or GPU-P state.
- Post-repair application-level evidence is real: the new Moonlight host ID is `944609277`; `/api/host` reports `Paired`, `server_state=Free`, guest address `100.111.87.90`, and Sunshine version `7.1.431.-1`; `/api/apps` enumerates `Desktop` and `Steam Big Picture`. The retained container is healthy and its digest is unchanged. This is not being represented as video-frame or input success.
- The existing authenticated Chrome route renders the Moonlight shell but initially shows an empty host grid. DevTools records `GET /api/hosts` failing, and Traefik access logs show the public request timing out at the Moonlight backend (`500` after 30 seconds) or being rejected by ForwardAuth (`503` after approximately 10 seconds). The dashboard ForwardAuth handler was found to force a full agent-side Gaming repair whenever `gamingCaptureConfigured=false`; the stale agent timeout therefore prevented the browser from reaching the already-paired host.
- The production source now permits only the ForwardAuth browser boundary to pass an application-level `verify_staged` result as `routeReady=true`, `pending=true`, and `visualValidationRequired=true` when the Gaming capture marker is absent. It does not set `gamingCaptureConfigured`, does not persist `ready`, and leaves the normal admin repair path fail-closed. A regression test covers this exact behavior.
- Direct KVM2-to-container verification of `/vm/gaming-gpup-pilot-03--epic-pc/api/hosts` with the scoped route identity returns HTTP 200 and the paired host; the public authenticated route still needs the new dashboard source deployed and then must be rechecked in Chrome. No final visual/input claim has been made.

## Source synchronization after browser-route fix

- New focused source/test changes are currently uncommitted and limited to `dashboard/app.py` and `tests/test_admin_vm_sso.py`; they are intended for the `production` branch only.
- Corrected focused test command (`PYTHONPATH='.;dashboard' uv run --with-requirements requirements-dev.txt pytest -q tests/test_admin_vm_sso.py tests/test_provisioning_api.py tests/test_moonlight_orchestrator.py`) passed `48` tests.
- The next safe action is to commit/push this focused dashboard fix, fast-forward `/opt/blobe-vm/repo`, capture deployment rollback state, build a unique dashboard image, and deploy only after local image validation. Then re-open the existing authenticated browser route and verify real frames/input; final readiness remains blocked until those checks and the updated Windows agent are both valid.

## Pending-visual deployment preparation

- KVM2 `/opt/blobe-vm/repo` is on `production` at `1d640688fb653a8147c4cb7e7ab0645f57732944`; the focused source patch was checked with `git apply --check` against the live dashboard before mutation.
- The live dashboard checkout contained unrelated local differences from the repository. I preserved those differences and applied only the focused production patch to `/opt/blobe-vm/dashboard/app.py`; `/opt/blobe-vm/dashboard/remote_agent_client.py` was backed up but unchanged by this commit.
- Redacted rollback snapshot and pre-change source files: `/opt/blobe-vm/recovery-backups/gaming-visual-route-1d64068-20260820T131739Z`. It contains the focused patch, `app.py.before`, `remote_agent_client.py.before`, and a container configuration snapshot with environment values omitted.
- After the patch, live hashes are: `dashboard/app.py` `6559d0b712c94c73b0f4e2b31751c6c0d46d30434935fcf5ab9ea0920d0f4c4a`; `dashboard/remote_agent_client.py` `cd7842fa4d5c237380cba58af64f44df676b80b01517de1e7537f19ff997fe0f`. The live app now contains the pending-visual browser boundary; final readiness remains fail-closed.
- Baseline rollback remains image `blobedash:moonlight-canary-08e36b8` / image ID `sha256:de0cb17f7b4c0030d2674ca1e9c300f7ad35dd98ff20e569bb4a278923a4740a` plus restoring the two backed-up live source files and restarting `blobedash.service`. No deployment restart has occurred yet.

## Pending-visual deployment result

- The unique image was built from KVM2 `/opt/blobe-vm/repo` at production source `1d640688fb653a8147c4cb7e7ab0645f57732944`; the read-only, network-disabled AST preflight passed for `app.py` and `remote_agent_client.py`.
- The dashboard was deployed conservatively through the supported `blobedash.service` ensure wrapper after changing only `EPICVM_BLOBEDASH_IMAGE` in `/opt/blobe-vm/.env`. The image selector is now the intended persistent value `blobedash:visual-route-1d64068`.
- Active deployment: tag `blobedash:visual-route-1d64068`; image ID/digest `sha256:f273200e8d38994e888576d7263b7603dcccbe295dc98e369132f72821c8f77c`; container started `2026-08-20T13:21:27.325991811Z`; state `running`; local protected dashboard API probe returned expected `401`.
- The bounded deploy script would have restored the backed-up source and prior image selector automatically if the new container failed its running/image/API checks; it did not need to roll back. The rollback snapshot also contains the old image-selector line in root-protected `env-image-setting.before`.
- No unrelated containers, routes, VMs, GPU assignments, or host services were changed by this deployment. Browser-frame and input acceptance are still unproven.

## Browser KVM/Moonlight validation after deployment

- The existing authenticated Chrome production route `https://techexplore.us/vm/gaming-gpup-pilot-03--epic-pc/` was reloaded in place; no login credentials were entered and no new browser profile was used.
- The route now renders the paired host card `DESKTOP-FI0I3JK`, then enumerates both intended applications `DESKTOP` and `STEAM`. This confirms the deployed ForwardAuth pending-visual boundary reaches the application-level Moonlight host/app path.
- Launching `DESKTOP` opened the stream page and reported `Web Socket Open`.
- The browser stream statistics reported `H264, 2560x1440, 60 fps`; `video pipeline: videotrack (transport) -> video_element (renderer)`; `webrtcPacketsReceived=67`; `webrtcPacketsLost=0`; `webrtcFramesDropped=0`; `webrtcKeyFramesDecoded=1`; and `webrtcNetworkCount=0` during the captured observation.
- Despite transport and decoder activity, the visible video canvas remained uniformly black after the initial connection and an additional ten-second observation. No real Windows desktop frame was accepted as evidence. Keyboard and mouse input have not been claimed or persisted.

## Moonlight runtime hypothesis and pinned image update

- The black-frame observation is now tied to a concrete retained-runtime failure: the pinned v2.10.0 client negotiated H.264 and received packets/keyframe metadata, then its control stream disconnected after approximately 22 seconds (`Control stream received unexpected disconnect event`; loss transaction failed). This is below public routing and above browser input acceptance; it is not treated as a successful stream.
- Static inspection of the retained image found the legacy frontend probes `../../libopenh264/decoder.js`, which is intentionally absent from the published image. The WebRTC track path was selected, but the bundle is stale relative to current upstream runtime behavior.
- Upstream Docker Hub evidence identified `v3.0.0-prerelease.4` (published 2026-08-19), multi-arch manifest `sha256:a5d806990a0f8cde29a4b696644e1660bc07a858b7aa92332682b67b754068ce`, AMD64 image digest `sha256:82cf429ffea07bdb30d3f8bf14e9e97a0a7186b0864ec4250b680b3c0c302d2b`. The newer bundle is flattened, includes its hashed OpenH264 WASM asset and decoder hook, and includes later WebRTC control-stream handling. This is evidence for a targeted client-runtime correction, not proof of end-to-end success.
- Tracked source change: `dashboard/moonlight_orchestrator.py` now defaults to the AMD64 digest-pinned `v3.0.0-prerelease.4` image. The existing `EPICVM_MOONLIGHT_IMAGE` override and explicit digest validation remain intact.
- Rollback for the retained instance is the captured `/opt/epicvm/moonlight-instances/gaming-gpup-pilot-03/docker-compose.yml` image line and the previous digest `sha256:694ca7e33266a56bf4c8bb29cb916b0927f126578dae4cf0710a881efce6564b`; the server volume and VM/VHDX/GPU state are not part of this change.
- Next intended action: run the focused Moonlight orchestrator tests, synchronize the tracked source, update only the retained Moonlight image through its existing compose project, verify application-level host/apps and real browser pixels/input, and restore the old image if startup, routing, or stream verification regresses.
- This narrows the remaining fault below public routing and WebRTC negotiation, at the guest display/capture/encoder output or an equivalent black-frame condition. `console-verify` was intentionally not called because the required visual and input evidence is absent.

## Current runtime, guest-session, and UDP evidence

- The retained Moonlight compose project is still the same project and now runs the AMD64 `v3.0.0-prerelease.4` image digest `sha256:82cf429ffea07bdb30d3f8bf14e9e97a0a7186b0864ec4250b680b3c0c302d2b`; the container is `running` and `healthy`. The paired host remains available and the intended `Desktop` and `Steam Big Picture` apps enumerate.
- The existing authenticated Chrome route was observed after the new runtime terminated its stream. The browser reported `Tried all configured transport options but no connection was possible`; its diagnostic log showed WebRTC configuration, transport creation, a connected ICE state, and no usable desktop frame. This is a live failure, not a page-load or database-state failure.
- New Moonlight logs show the WebRTC peer and stream were created for H.264 at `2560x1440`/60 fps, the control/video/audio streams were started, ICE reached `Connected`, and a first video packet was received. The stream then terminated with `control: the control stream hasn't successfully connected yet`. This confirms that route/authentication and initial WebRTC negotiation are not sufficient; the browser still received no usable continuous desktop.
- Safe Sunshine API evidence from the retained guest shows Sunshine `2026.516.143833`, the intended Desktop and Steam applications, AMD Radeon RX 6800 XT (`0x1002:0x73BF`), a real `h264_amf` encoder initialization through AMF/D3D11, and capture dimensions `1024x768`. During the attempted stream Sunshine logged repeated UDP receive failures reporting that the target machine actively refused the connection, followed by `CLIENT DISCONNECTED`. The scoped guest Sunshine TCP/UDP firewall rules were present and enabled for the Tailscale range; this does not prove the stream path is healthy.
- A bounded supplemental WinRM diagnostic was run directly from KVM2 using the protected dashboard-side guest credential bundle; credentials were used in memory only and were not printed or journaled. This is diagnostic evidence only and does not satisfy the required LocalSystem-agent transport acceptance.
- The guest authoritative session evidence is now explicit: `Win32_ComputerSystem.UserName` is empty; `query user` returned no user session; the only `winlogon.exe` instance is `SYSTEM` in session 1; `dwm.exe` exists only in that session; no `explorer.exe` process exists; and no interactive user owns the desktop. The AMD display device is `OK`, but the active video-mode report is on `Microsoft Hyper-V Video` while Sunshine reports a `1024x768` virtual desktop.
- Current root-cause hypothesis: the retained VM has no logged-on Windows user desktop for Sunshine to capture, while the browser path also has a separate UDP/control-stream failure. The source-side `renderFrame`/WebGL probe and AMF log marker can therefore be true without proving that Sunshine is streaming real user desktop pixels. No interactive logon, AutoAdminLogon, RunOnce, PsExec, PowerShell Direct, or other prohibited bypass will be used.
- `gamingCaptureConfigured` remains untrusted for final readiness until a real browser frame and input are proven. `console-verify` and the required retained-VM stop/start remain intentionally deferred. Next action is to inspect the supported non-interactive guest/display path and determine whether the missing desktop session/UDP control failure can be corrected within the stated safety boundaries; otherwise record the exact external blocker.

## WebRTC UDP publication recovery slice

- New rollback capture for the retained Moonlight instance: `/opt/blobe-vm/recovery-backups/moonlight-webrtc-udp-20260820T142604Z`. It contains the pre-change retained `docker-compose.yml` and a redacted container baseline; the prior runtime digest, server volume, VM/VHDX/GPU state, and route were not changed by this capture.
- Confirmed external/runtime evidence: upstream Moonlight Web documentation requires the configured WebRTC UDP range to be published by Docker, and the retained compose project had no `ports` section. The retained server config used `41000:41010`; `docker port` exposed no UDP mapping.
- Tracked source changes, currently uncommitted, are limited to the WebRTC transport contract: `dashboard/moonlight_orchestrator.py` now publishes UDP `41000-41010`, optionally emits the upstream `nat_1to1`/`WEBRTC_NAT_1TO1_HOST` setting, and rejects NAT hosts outside the Tailscale IPv4 range. `server/blobedash-ensure.sh` and `server/install.sh` forward/persist `EPICVM_MOONLIGHT_NAT_HOST` without writing credentials.
- Regression tests were written first and observed red, then passed after implementation: affected Moonlight and service-metadata tests `22 passed`; `bash -n server/blobedash-ensure.sh server/install.sh` and `python -m py_compile dashboard/moonlight_orchestrator.py` passed.
- The KVM2 host Tailscale address selected for this retained route is `100.89.87.98`; the public `72.60.29.204` address is intentionally rejected by the source validation. The live retained compose and dashboard image have not yet been mutated for this slice.
- Current hypothesis remains two-layered: missing Docker UDP publication can explain the WebRTC/control failure, but the guest still has no interactive Windows desktop session (`UserName` empty, no explorer) and that remains an independent acceptance blocker unless a supported non-interactive path is found. No VM restart or browser readiness claim is made.
- Next action: run the broader source checks, commit/push the focused change, build/deploy the dashboard conservatively, update only the retained Moonlight compose/config with rollback available, and re-test the existing browser route for real pixels and input.

## WebRTC dashboard deployment attempt and KVM2 control-plane blocker

- Source validation completed locally for production commit `0f31510`: focused Python recovery suite `102 passed, 1 skipped`; Pester 5.7.1 Gaming/provisioning/provider suites `113 passed, 0 failed, 0 skipped`; PowerShell parser passed all 9 tracked entrypoints/providers; shell syntax and Python compilation passed.
- KVM2 built the unique dashboard image `blobedash:webrtc-udp-0f31510`; build output reported image ID/digest `sha256:f107980506b307e6043bb4104f52bc7d99130fb80c9de4615286db07ddeb830d`. A rollback directory was captured at `/opt/blobe-vm/recovery-backups/blobedash-webrtc-udp-0f31510-20260820T143609Z`, including the pre-change live ensure wrapper and non-secret selector state.
- The live wrapper was patched only to forward `EPICVM_MOONLIGHT_NAT_HOST`; the pre-existing unrelated `/EpicVM` route-label differences were preserved. The root environment was staged with the new image selector and the Tailscale NAT host, but deployment verification did not complete.
- `systemctl reload blobedash.service` entered `reloading` with control PID `1831564` running the ensure wrapper. The old container `blobedash:visual-route-1d64068` remained running. A bounded `docker info`/inspection call hung, and subsequent public SSH/HTTPS and Tailscale SSH/ping attempts intermittently timed out or accepted TCP without an SSH banner/HTTP response. This is an external KVM2 Docker/control-plane availability blocker, not evidence that the new image is active.
- No retained VM, VHDX, GPU-P adapter, unrelated container, or unrelated route was restarted or changed during this failed deployment attempt. The retained browser still showed the previously observed black frame; no visual/input acceptance claim was made.
- A bounded recovery watcher is attempting only to terminate the stuck dashboard ensure PID, restore the captured pre-deploy wrapper and selector variables without invoking Docker, and verify the service state. It performs no broad process kill and no VM operation. Its final outcome must be recorded before completion.
- The full Python suite was also run: `208 passed, 19 failed, 1 skipped`. The failures are the previously identified Windows-environment/baseline groups (POSIX mode assertions, executable subprocess launch, nested-docker launcher execution, and legacy public-brand assertions); none are in the focused recovery suite. `git diff --check` passed with only the expected Windows LF/CRLF warning.
- The recovery watcher succeeded on attempt 4: it terminated only PID `1831564`, restored the pre-deploy wrapper and the captured selector state without invoking Docker, and reported `blobedash.service=active`. The new image is therefore not active and the WebRTC dashboard deployment remains rolled back/pending the KVM2 Docker-control-plane recovery.

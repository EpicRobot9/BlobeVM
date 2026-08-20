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

## Current observed state after implementation slice

- Retained VM remains unchanged and stopped; no replacement VM was created.
- Installed Windows agent remains the pre-change installation until the supported installer is run after final source/test review.
- Production dashboard remains on the baseline image until the dashboard source is committed, synchronized to KVM2, built with a unique tag, and live-verified with rollback available.
- The persisted Gaming record remains non-ready; this is intentional until the real browser frame and both input channels are proven.

## Next intended action

Run the full relevant Pester 5 suite and source checks, inspect the supported installer/configuration path, then update the retained LocalSystem agent from the production checkout while preserving its existing safe configuration and adding only the retained Gaming VM to the explicit Gaming name allowlist if evidence requires it. After that, start the retained VM through the supported path and gather live AMD/display/Sunshine/WinRM evidence without PowerShell Direct.

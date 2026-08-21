# Moonlight Web WebRTC close-race overlay

This directory builds the EpicVM-pinned Moonlight Web image with one narrowly
scoped frontend fix. The upstream ENet output poller could call
`RTCDataChannel.send()` after the browser closed the channel. That raised
`InvalidStateError` in the client and could leave a failed stream worker
spinning. The patch is deliberately tied to the pinned bundle and fails closed
if its minified shape changes.

## Build

From the repository root on the deployment host:

```bash
docker build -f docker/moonlight-web/Dockerfile \
  -t epicvm/moonlight-web:webrtc-close-guard-<source-tag> \
  docker/moonlight-web
```

The deployment's `/opt/blobe-vm/.env` must set
`EPICVM_MOONLIGHT_IMAGE` to the resulting local tag. The normal orchestrator
still requires a digest-pinned image when it receives a registry reference;
the local overlay tag is intentionally scoped to the host where it was built.
Record the image ID and rollback to the prior digest-pinned image if live
verification fails.

## Verification

The overlay is not a readiness shortcut. Verify all of the following through
the authenticated browser path:

- the browser receives decoded, changing H.264 frames;
- the screenshot is a real Windows lock/sign-in screen or desktop, not black;
- keyboard and mouse input change the guest image;
- closing the browser does not produce the unguarded `RTCDataChannel.send`
  exception or a sustained Moonlight CPU worker;
- the retained VM identity and GPU-P configuration remain unchanged.

"""Patch the pinned Moonlight Web bundle's ENet send-close race.

The upstream bundle polls ENet output from a timer.  Its normal send path
checks RTCDataChannel.readyState, but the timer path did not.  When a browser
closes a stream, the timer could call send() on a closed channel, raising
InvalidStateError and leaving a failed worker spinning.

This script intentionally fails closed when the pinned upstream bundle changes.
It is used by Dockerfile to produce a reproducible EpicVM overlay image.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

OLD = 'for(;r=this.controlStream.pollPacket();)console.debug(r.contents,"enet send"),this.channel.send(r.contents);'
NEW = 'for(;r=this.controlStream.pollPacket();){if(!this.channel||"open"!=this.channel.readyState)return;console.debug(r.contents,"enet send"),this.channel.send(r.contents)}'


def patch_file(path: str | Path) -> bool:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if NEW in text and OLD not in text:
        return False
    count = text.count(OLD)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one pinned Moonlight ENet poll loop in {target}, found {count}"
        )
    patched = text.replace(OLD, NEW, 1)
    if NEW not in patched or OLD in patched:
        raise RuntimeError(f"Moonlight ENet close-race patch verification failed for {target}")
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(patched)
        # Windows refuses replacing a read-only target.  Temporarily grant the
        # owner write permission, then restore the original mode on the new
        # inode.  Linux builders can replace the target directly.
        os.chmod(temporary, mode)
        os.chmod(target, mode | stat.S_IWUSR)
        os.replace(temporary, target)
        os.chmod(target, mode)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} STREAM_JS", file=sys.stderr)
        return 2
    try:
        changed = patch_file(argv[1])
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("patched" if changed else "already-patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

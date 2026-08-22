#!/usr/bin/env bash
# Regression coverage for the moonlight overlay patcher: close-race guard +
# stream-start watchdog, idempotency, and fail-closed behavior.
set -e
cd "$(dirname "$0")/.."   # repo root (script lives in tests/)
TMP="$(cygpath -m "$HOME" 2>/dev/null || echo "$HOME")/.cache/epicvm-test-tmp-$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT
python - "$TMP" <<'PYEOF'
import subprocess, sys, os, pathlib
tmp = pathlib.Path(sys.argv[1]).resolve()
tmp = pathlib.Path(os.path.abspath(str(tmp)))
patcher = pathlib.Path("docker/moonlight-web/patch_stream.py")

OLD = 'for(;r=this.controlStream.pollPacket();)console.debug(r.contents,"enet send"),this.channel.send(r.contents);'
PLAY_GATE = 'onUserInteraction(){this.videoElement.paused&&this.videoElement.play().then(()=>{}).catch(e=>{console.error(`Failed to play videoElement: ${e.message||e}`)})}'
bundle = (
    '(()=>{"use strict";'
    'class A{' + PLAY_GATE + '}'
    'class B{' + PLAY_GATE + '}'
    'let r,i;' + OLD + 'console.log("rest");})();'
)
src = tmp / "bundle.js"
src.write_text(bundle, encoding="utf-8")

def run():
    return subprocess.run([sys.executable, str(patcher), str(src)], capture_output=True, text=True)

first = run()
assert first.returncode == 0 and first.stdout.strip() == "patched", (first.returncode, first.stdout, first.stderr)
second = run()
assert second.returncode == 0 and second.stdout.strip() == "already-patched", (second.returncode, second.stdout, second.stderr)

text = src.read_text(encoding="utf-8")
assert OLD not in text, "close-race guard not applied"
assert text.count('if(!this.channel||"open"!=this.channel.readyState)return') == 1
assert text.count('epicvmArmFrameWatchdog(this);') == 2, "watchdog must arm on both video sinks"
assert text.count('function epicvmArmFrameWatchdog') == 1

# Fail closed when the pinned shape changes.
mutated = tmp / "mutated.js"
mutated.write_text('console.log("unrelated bundle");', encoding="utf-8")
bad = subprocess.run([sys.executable, str(patcher), str(mutated)], capture_output=True, text=True)
assert bad.returncode != 0 and "poll loop" in bad.stderr, (bad.returncode, bad.stderr)

print("patch_stream overlay tests: PASS")
PYEOF

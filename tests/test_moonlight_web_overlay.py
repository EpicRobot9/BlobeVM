import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = REPO_ROOT / "docker" / "moonlight-web" / "patch_stream.py"


spec = importlib.util.spec_from_file_location("epicvm_moonlight_patch_stream", PATCH_PATH)
assert spec and spec.loader
patch_stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_stream)


def test_enet_close_guard_patches_the_pinned_bundle_once(tmp_path):
    bundle = tmp_path / "stream.js"
    bundle.write_text("prefix" + patch_stream.OLD + "suffix", encoding="utf-8")

    assert patch_stream.patch_file(bundle) is True
    patched = bundle.read_text(encoding="utf-8")
    assert patch_stream.OLD not in patched
    assert patch_stream.NEW in patched
    assert "readyState" in patched

    # A second build step is harmless and does not duplicate the overlay.
    assert patch_stream.patch_file(bundle) is False
    assert bundle.read_text(encoding="utf-8") == patched


def test_enet_close_guard_handles_read_only_bundle_mode(tmp_path):
    bundle = tmp_path / "stream.js"
    bundle.write_text("prefix" + patch_stream.OLD + "suffix", encoding="utf-8")
    bundle.chmod(0o444)

    assert patch_stream.patch_file(bundle) is True
    assert patch_stream.NEW in bundle.read_text(encoding="utf-8")
    assert bundle.stat().st_mode & 0o777 == 0o444


def test_enet_close_guard_fails_closed_when_bundle_shape_changes(tmp_path):
    bundle = tmp_path / "stream.js"
    bundle.write_text("unrelated bundle", encoding="utf-8")

    try:
        patch_stream.patch_file(bundle)
    except RuntimeError as exc:
        assert "expected exactly one" in str(exc)
    else:
        raise AssertionError("bundle-shape drift must fail closed")


def test_overlay_dockerfile_is_pinned_and_preserves_nonroot_runtime():
    dockerfile = (REPO_ROOT / "docker" / "moonlight-web" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG BASE_IMAGE=" in dockerfile
    assert "@sha256:" in dockerfile
    assert "COPY --from=upstream /moonlight-web/static/stream.js" in dockerfile
    assert "COPY patch_stream.py /tmp/patch_stream.py" in dockerfile
    assert "RUN python /tmp/patch_stream.py /tmp/stream.js" in dockerfile
    assert "chown 999:999" in dockerfile
    assert "USER 999:999" in dockerfile

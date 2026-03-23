from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(**kwargs) -> None:
        return None


load_dotenv(dotenv_path='.env')


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


@dataclass(frozen=True)
class UpdateAgentConfig:
    enabled: bool
    project_root: Path
    inbox_dir: Path
    applied_dir: Path
    failed_dir: Path
    poll_seconds: int
    apply_script: Path
    healthcheck_cmd: str
    once: bool
    require_manifest: bool
    allowed_bundle_types: tuple[str, ...]
    require_sha256: bool


Runner = Callable[[Sequence[str]], int]


def load_config(env: dict[str, str] | None = None) -> UpdateAgentConfig:
    source = env or dict(os.environ)
    project_root = Path(source.get("UPDATE_AGENT_PROJECT_ROOT", Path(__file__).resolve().parent))
    inbox_dir = Path(source.get("UPDATE_AGENT_INBOX_DIR", project_root / "update" / "inbox"))
    applied_dir = Path(source.get("UPDATE_AGENT_APPLIED_DIR", project_root / "update" / "applied"))
    failed_dir = Path(source.get("UPDATE_AGENT_FAILED_DIR", project_root / "update" / "failed"))
    apply_script = Path(
        source.get(
            "UPDATE_AGENT_APPLY_SCRIPT",
            project_root / "device_config" / "apply_update_bundle.sh",
        )
    )
    return UpdateAgentConfig(
        enabled=_as_bool(source.get("UPDATE_AGENT_ENABLED"), True),
        project_root=project_root,
        inbox_dir=inbox_dir,
        applied_dir=applied_dir,
        failed_dir=failed_dir,
        poll_seconds=_as_int(
            source.get("UPDATE_AGENT_POLL_SECONDS"),
            15,
            minimum=3,
            maximum=3600,
        ),
        apply_script=apply_script,
        healthcheck_cmd=(source.get("UPDATE_AGENT_HEALTHCHECK_CMD") or "").strip(),
        once=_as_bool(source.get("UPDATE_AGENT_ONCE"), False),
        require_manifest=_as_bool(source.get("UPDATE_AGENT_REQUIRE_MANIFEST"), True),
        allowed_bundle_types=tuple(
            item.strip()
            for item in (
                source.get("UPDATE_AGENT_ALLOWED_BUNDLE_TYPES")
                or "matterhub-runtime,matterhub-update"
            ).split(",")
            if item.strip()
        ),
        require_sha256=_as_bool(source.get("UPDATE_AGENT_REQUIRE_SHA256"), False),
    )


def _default_runner(command: Sequence[str]) -> int:
    completed = subprocess.run(list(command), check=False)
    return int(completed.returncode)


def discover_bundles(inbox_dir: Path) -> list[Path]:
    if not inbox_dir.exists():
        return []
    bundles = [path for path in inbox_dir.iterdir() if path.is_file() and path.suffixes[-2:] == [".tar", ".gz"]]
    bundles.sort(key=lambda path: path.stat().st_mtime)
    return bundles


def _sha256_path(bundle_path: Path) -> Path:
    return bundle_path.with_name(f"{bundle_path.name}.sha256")


def _calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_sidecar_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    token = text.split()[0].strip().lower()
    if len(token) != 64:
        return ""
    if any(ch not in "0123456789abcdef" for ch in token):
        return ""
    return token


def verify_bundle(bundle_path: Path, config: UpdateAgentConfig) -> tuple[bool, str]:
    if config.require_sha256:
        sidecar = _sha256_path(bundle_path)
        expected = _read_sidecar_sha256(sidecar)
        if not expected:
            return False, "sha256_sidecar_missing_or_invalid"
        actual = _calculate_sha256(bundle_path)
        if actual != expected:
            return False, "sha256_mismatch"

    try:
        with tarfile.open(bundle_path, "r:gz") as archive:
            members = archive.getmembers()
            names = {member.name for member in members}
            if not any(name.startswith("payload/") for name in names):
                return False, "payload_missing"
            if config.require_manifest:
                manifest_candidates = [
                    name for name in names if name == "manifest.json" or name.endswith("/manifest.json")
                ]
                if not manifest_candidates:
                    return False, "manifest_missing"
                manifest_member = archive.extractfile(manifest_candidates[0])
                if manifest_member is None:
                    return False, "manifest_read_failed"
                try:
                    manifest = json.loads(manifest_member.read().decode("utf-8"))
                except Exception:
                    return False, "manifest_parse_failed"
                bundle_type = str(manifest.get("bundle_type") or "").strip()
                if config.allowed_bundle_types and bundle_type not in config.allowed_bundle_types:
                    return False, "bundle_type_not_allowed"
    except tarfile.TarError:
        return False, "invalid_tar_gz"
    return True, "ok"


def _archive_bundle(bundle_path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    destination = target_dir / f"{timestamp}-{bundle_path.name}"
    shutil.move(str(bundle_path), str(destination))
    sidecar = _sha256_path(bundle_path)
    if sidecar.is_file():
        shutil.move(str(sidecar), str(destination.with_name(f"{destination.name}.sha256")))
    return destination


def _build_apply_command(config: UpdateAgentConfig, bundle_path: Path) -> list[str]:
    command = [
        "bash",
        str(config.apply_script),
        "--bundle",
        str(bundle_path),
        "--project-root",
        str(config.project_root),
    ]
    if config.healthcheck_cmd:
        command.extend(["--healthcheck-cmd", config.healthcheck_cmd])
    return command


def process_once(config: UpdateAgentConfig, runner: Runner = _default_runner) -> int:
    if not config.enabled:
        print("[UPDATE_AGENT] disabled (UPDATE_AGENT_ENABLED=0)")
        return 0

    if not config.apply_script.is_file():
        print(f"[UPDATE_AGENT][FAIL] apply script not found: {config.apply_script}")
        return 2

    bundles = discover_bundles(config.inbox_dir)
    if not bundles:
        print(f"[UPDATE_AGENT] no bundle in inbox: {config.inbox_dir}")
        return 0

    overall_rc = 0
    for bundle_path in bundles:
        verified, reason = verify_bundle(bundle_path, config)
        if not verified:
            archived = _archive_bundle(bundle_path, config.failed_dir)
            print(f"[UPDATE_AGENT][FAIL] verify={reason} -> {archived}")
            overall_rc = 4
            continue
        command = _build_apply_command(config, bundle_path)
        print(f"[UPDATE_AGENT] applying bundle: {bundle_path.name}")
        print(f"[UPDATE_AGENT] command={' '.join(command)}")
        rc = runner(command)
        if rc == 0:
            archived = _archive_bundle(bundle_path, config.applied_dir)
            print(f"[UPDATE_AGENT][OK] applied bundle -> {archived}")
        else:
            archived = _archive_bundle(bundle_path, config.failed_dir)
            print(f"[UPDATE_AGENT][FAIL] apply rc={rc} -> {archived}")
            overall_rc = rc
    return overall_rc


def download_bundle(url: str, inbox_dir: Path, sha256_hint: str = "", timeout: int = 120) -> Path:
    """URL에서 번들 다운로드 → inbox_dir에 저장 → 파일 경로 반환.

    sha256_hint가 주어지면 .sha256 사이드카 파일도 함께 생성한다.
    URL 끝에 .sha256 파일이 있으면 자동으로 다운로드 시도한다.
    """
    import urllib.request

    inbox_dir.mkdir(parents=True, exist_ok=True)

    # 파일명 추출
    filename = url.rsplit("/", 1)[-1] if "/" in url else ""
    if not filename or not filename.endswith(".tar.gz"):
        filename = f"bundle_{int(time.time())}.tar.gz"

    dest = inbox_dir / filename

    print(f"[UPDATE_AGENT] downloading bundle: {url} -> {dest}")
    urllib.request.urlretrieve(url, str(dest))
    print(f"[UPDATE_AGENT] download complete: {dest} ({dest.stat().st_size} bytes)")

    # SHA256 사이드카 처리
    sidecar = _sha256_path(dest)
    if sha256_hint:
        sidecar.write_text(sha256_hint + "\n", encoding="utf-8")
        print(f"[UPDATE_AGENT] sha256 sidecar written from hint: {sidecar}")
    else:
        # URL+".sha256" 에서 사이드카 다운로드 시도
        try:
            urllib.request.urlretrieve(url + ".sha256", str(sidecar))
            print(f"[UPDATE_AGENT] sha256 sidecar downloaded: {sidecar}")
        except Exception:
            pass  # 사이드카 없어도 OK (require_sha256=False가 기본)

    return dest


def list_inbox(inbox_dir: Path) -> list[dict[str, Any]]:
    """inbox 디렉토리의 번들 목록 반환 (상태 확인용)."""
    bundles = discover_bundles(inbox_dir)
    result = []
    for b in bundles:
        result.append({
            "name": b.name,
            "size": b.stat().st_size,
            "mtime": int(b.stat().st_mtime),
        })
    return result


def run_forever(config: UpdateAgentConfig, runner: Runner = _default_runner) -> int:
    while True:
        process_once(config, runner=runner)
        if config.once:
            return 0
        time.sleep(config.poll_seconds)


def main() -> int:
    config = load_config()
    return run_forever(config)


if __name__ == "__main__":
    raise SystemExit(main())

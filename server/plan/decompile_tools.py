"""Provision Vineflower + tiny-remapper for closed-source reference decompile."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from plan.reference_config import (
    TINY_REMAPPER_JAR,
    TINY_REMAPPER_MIN_BYTES,
    TINY_REMAPPER_URL,
    TINY_REMAPPER_VERSION,
    TOOLS_DIR,
    VINEFLOWER_JAR,
    VINEFLOWER_URL,
    VINEFLOWER_VERSION,
)
from storage.file_io import ensure_dir, read_json, write_json

logger = logging.getLogger(__name__)

MANIFEST_PATH = TOOLS_DIR / "tools_manifest.json"


@dataclass
class DecompileToolsStatus:
    java_ok: bool
    vineflower_ok: bool
    tiny_remapper_ok: bool
    tools_dir: str
    vineflower_version: str
    tiny_remapper_version: str
    error: str | None = None


def check_java() -> bool:
    return shutil.which("java") is not None


def _jar_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 1024
    except OSError:
        return False


def _download_file(url: str, dest: Path, *, timeout: float = 120.0) -> None:
    ensure_dir(dest.parent)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as res:
            if res.status_code != 200:
                raise RuntimeError(f"下载失败 HTTP {res.status_code}: {url}")
            with tmp.open("wb") as fh:
                for chunk in res.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
    if tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"下载文件过小: {url}")
    tmp.replace(dest)


def _tiny_remapper_ok() -> bool:
    try:
        return TINY_REMAPPER_JAR.is_file() and TINY_REMAPPER_JAR.stat().st_size >= TINY_REMAPPER_MIN_BYTES
    except OSError:
        return False


def ensure_decompile_tools(*, auto_download: bool = True) -> DecompileToolsStatus:
    """Ensure vineflower + tiny-remapper jars exist under DATA_DIR/tools/."""
    ensure_dir(TOOLS_DIR)
    if TINY_REMAPPER_JAR.is_file() and not _tiny_remapper_ok():
        logger.warning("Removing non-fat tiny-remapper jar (too small), will re-download fat jar")
        TINY_REMAPPER_JAR.unlink(missing_ok=True)
    status = get_decompile_tools_status()

    if status.vineflower_ok and status.tiny_remapper_ok:
        return status

    if not auto_download:
        return status

    manifest: dict = read_json(MANIFEST_PATH) or {}
    errors: list[str] = []

    if not status.vineflower_ok:
        try:
            logger.info("Downloading Vineflower %s -> %s", VINEFLOWER_VERSION, VINEFLOWER_JAR)
            _download_file(VINEFLOWER_URL, VINEFLOWER_JAR)
            manifest["vineflower_version"] = VINEFLOWER_VERSION
        except Exception as exc:
            errors.append(f"Vineflower: {exc}")

    if not status.tiny_remapper_ok:
        try:
            logger.info("Downloading tiny-remapper %s -> %s", TINY_REMAPPER_VERSION, TINY_REMAPPER_JAR)
            _download_file(TINY_REMAPPER_URL, TINY_REMAPPER_JAR)
            manifest["tiny_remapper_version"] = TINY_REMAPPER_VERSION
        except Exception as exc:
            errors.append(f"tiny-remapper: {exc}")

    if manifest:
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(MANIFEST_PATH, manifest)

    final = get_decompile_tools_status()
    if errors:
        final.error = "；".join(errors)
    return final


def get_decompile_tools_status() -> DecompileToolsStatus:
    manifest = read_json(MANIFEST_PATH) or {}
    return DecompileToolsStatus(
        java_ok=check_java(),
        vineflower_ok=_jar_ok(VINEFLOWER_JAR),
        tiny_remapper_ok=_tiny_remapper_ok(),
        tools_dir=str(TOOLS_DIR),
        vineflower_version=str(manifest.get("vineflower_version") or VINEFLOWER_VERSION),
        tiny_remapper_version=str(manifest.get("tiny_remapper_version") or TINY_REMAPPER_VERSION),
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Ensure plan decompile tool jars")
    parser.add_argument("--ensure", action="store_true", help="Download missing jars")
    args = parser.parse_args()
    if args.ensure:
        st = ensure_decompile_tools(auto_download=True)
        print(json.dumps(st.__dict__, ensure_ascii=False, indent=2))
        return 0 if st.vineflower_ok and st.tiny_remapper_ok else 1
    st = get_decompile_tools_status()
    print(json.dumps(st.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

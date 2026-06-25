"""Decompile closed-source Fabric reference mods: Modrinth jar + Yarn remap + Vineflower."""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Literal

RefScope = Literal["plan", "session"]

import httpx

from modrinth_client import (
    ModrinthError,
    download_version_file,
    fetch_matching_version,
    pick_primary_jar_file,
)
from plan.decompile_tools import ensure_decompile_tools, get_decompile_tools_status
from plan.reference_config import (
    DECOMPILE_TIMEOUT_SEC,
    MAX_ARTIFACT_MB,
    TINY_REMAPPER_JAR,
    VINEFLOWER_JAR,
)
from storage.file_io import ensure_dir, write_json

logger = logging.getLogger(__name__)

FABRIC_META_YARN = "https://meta.fabricmc.net/v2/versions/yarn/{mc_version}"

RefStepCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

_current_decompile_log: Path | None = None


def _count_java_files(repo: Path) -> int:
    if not repo.is_dir():
        return 0
    return sum(1 for p in repo.rglob("*.java") if p.is_file())


def _validate_repo_has_sources(repo: Path) -> None:
    java_n = _count_java_files(repo)
    if java_n == 0:
        raise DecompileError(
            "反编译未产生可索引源码（0 个文件）",
            failure_kind="decompile_error",
        )


def _append_decompile_log(text: str) -> None:
    path = _current_decompile_log
    if not path or not text:
        return
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def _jar_class_count(jar_path: Path) -> int:
    try:
        with zipfile.ZipFile(jar_path) as zf:
            return sum(1 for n in zf.namelist() if n.endswith(".class"))
    except (OSError, zipfile.BadZipFile):
        return 0


@dataclass
class DecompileResult:
    source_kind: str
    mapping: str | None
    yarn_build: str | None
    version_id: str | None
    game_version: str
    loader: str
    degrade_reason: str | None = None


class ReferenceDecompileBackend(Protocol):
    def decompile_and_remap(
        self,
        jar: Path,
        out_repo: Path,
        *,
        mc_version: str,
        work_dir: Path,
    ) -> DecompileResult: ...


class DecompileError(RuntimeError):
    """Fixed-program decompile/remap failure."""

    def __init__(self, message: str, *, failure_kind: str | None = None):
        super().__init__(message)
        self.failure_kind = failure_kind or "decompile_error"


async def _step(
    on_step: RefStepCallback | None,
    *,
    project_id: str,
    title: str,
    step: str,
    label: str,
    status: str,
    preview: str = "",
) -> None:
    if not on_step:
        return
    payload = {
        "project_id": project_id,
        "title": title,
        "step": step,
        "label": label,
        "status": status,
        "preview": preview,
    }
    result = on_step(payload)
    if result is not None:
        await result


def _java_bin() -> str:
    java = shutil.which("java")
    if not java:
        raise DecompileError("未找到 Java 17+ 运行时，无法反编译参考模组", failure_kind="missing_java")
    return java


def _require_vineflower() -> Path:
    if not VINEFLOWER_JAR.is_file():
        raise DecompileError(
            "未找到 Vineflower，请检查网络或 data/tools/vineflower.jar",
            failure_kind="missing_tools",
        )
    return VINEFLOWER_JAR


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DECOMPILE_TIMEOUT_SEC,
    label: str = "subprocess",
) -> None:
    cmd_str = " ".join(cmd)
    logger.info("decompile [%s]: %s", label, cmd_str)
    _append_decompile_log(f"\n--- {label} ---\n$ {cmd_str}\n")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )
    combined = "\n".join(filter(None, [proc.stdout, proc.stderr]))
    if combined.strip():
        _append_decompile_log(combined)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "command failed").strip()
        logger.error("decompile [%s] failed (rc=%s): %s", label, proc.returncode, err[-500:])
        raise DecompileError(err[:500], failure_kind="decompile_error")
    logger.info("decompile [%s] ok", label)


def fetch_yarn_mappings_tiny(mc_version: str, cache_dir: Path) -> tuple[Path, str]:
    """Download Yarn tiny mappings; returns (path, build version string)."""
    ensure_dir(cache_dir)
    cached = cache_dir / f"yarn-{mc_version}.tiny"
    meta_cached = cache_dir / f"yarn-{mc_version}.json"
    if cached.is_file() and meta_cached.is_file():
        meta = json.loads(meta_cached.read_text(encoding="utf-8"))
        return cached, meta.get("version") or mc_version

    url = FABRIC_META_YARN.format(mc_version=mc_version)
    with httpx.Client(timeout=30.0) as client:
        res = client.get(url)
        if res.status_code != 200:
            raise DecompileError(f"无法获取 Yarn 映射（MC {mc_version}）", failure_kind="decompile_error")
        entries = res.json()
        if not entries:
            raise DecompileError(f"Fabric 无 MC {mc_version} 的 Yarn 映射", failure_kind="decompile_error")
        stable = next((e for e in entries if e.get("stable")), entries[0])
        yarn_version = stable.get("version") or f"{mc_version}+build.1"

    maven_url = (
        f"https://maven.fabricmc.net/net/fabricmc/yarn/{yarn_version}/"
        f"yarn-{yarn_version}-v2.jar"
    )
    jar_bytes = httpx.get(maven_url, timeout=60.0, follow_redirects=True).content
    tiny_text = None
    with zipfile.ZipFile(io.BytesIO(jar_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith("mappings/mappings.tiny") or name.endswith("mappings.tiny"):
                tiny_text = zf.read(name).decode("utf-8", errors="replace")
                break
    if not tiny_text:
        raise DecompileError("Yarn 映射包中未找到 mappings.tiny", failure_kind="decompile_error")

    cached.write_text(tiny_text, encoding="utf-8")
    meta_cached.write_text(json.dumps({"version": yarn_version}, ensure_ascii=False), encoding="utf-8")
    return cached, yarn_version


def remap_jar_with_yarn(
    input_jar: Path,
    output_jar: Path,
    mappings_tiny: Path,
    *,
    work_dir: Path,
) -> None:
    if not TINY_REMAPPER_JAR.is_file():
        raise DecompileError(
            "未找到 tiny-remapper，请检查网络或 data/tools/tiny-remapper.jar",
            failure_kind="missing_tools",
        )
    if output_jar.exists():
        output_jar.unlink()
    _run(
        [
            _java_bin(),
            "-jar",
            str(TINY_REMAPPER_JAR),
            str(input_jar),
            str(output_jar),
            str(mappings_tiny),
            "intermediary",
            "named",
        ],
        cwd=work_dir,
        label="tiny-remapper",
    )


def decompile_jar_to_repo(jar: Path, out_repo: Path) -> None:
    vineflower = _require_vineflower()
    if out_repo.exists():
        shutil.rmtree(out_repo, ignore_errors=True)
    ensure_dir(out_repo)
    cmd = [
        _java_bin(),
        "-jar",
        str(vineflower),
        "--log-level=warn",
        "--remove-bridge=false",
        str(jar),
        str(out_repo),
    ]
    _run(cmd, label="vineflower")
    java_n = _count_java_files(out_repo)
    _validate_repo_has_sources(out_repo)
    logger.info("vineflower output: %s java files in %s", java_n, out_repo)


class FabricYarnBackend:
    """Remap intermediary jar to named, then decompile with Vineflower."""

    def decompile_and_remap(
        self,
        jar: Path,
        out_repo: Path,
        *,
        mc_version: str,
        work_dir: Path,
    ) -> DecompileResult:
        ensure_dir(work_dir)
        mappings_cache = work_dir / "mappings_cache"
        remapped = work_dir / "remapped.jar"

        yarn_build: str | None = None
        try:
            mappings, yarn_build = fetch_yarn_mappings_tiny(mc_version, mappings_cache)
            remap_jar_with_yarn(jar, remapped, mappings, work_dir=work_dir)
            decompile_jar_to_repo(remapped, out_repo)
            return DecompileResult(
                source_kind="decompiled",
                mapping="yarn",
                yarn_build=yarn_build,
                version_id=None,
                game_version=mc_version,
                loader="fabric",
            )
        except DecompileError as remap_exc:
            if remap_exc.failure_kind in ("missing_java", "missing_tools"):
                raise
            logger.warning("Yarn remap failed, falling back to direct decompile: %s", remap_exc)
            try:
                decompile_jar_to_repo(jar, out_repo)
                return DecompileResult(
                    source_kind="decompiled_obfuscated",
                    mapping=None,
                    yarn_build=yarn_build,
                    version_id=None,
                    game_version=mc_version,
                    loader="fabric",
                    degrade_reason="yarn_remap_failed",
                )
            except DecompileError:
                raise


_default_backend: FabricYarnBackend | None = None


def get_decompile_backend() -> ReferenceDecompileBackend:
    global _default_backend
    if _default_backend is None:
        _default_backend = FabricYarnBackend()
    return _default_backend


async def materialize_decompiled(
    user_id: str,
    scope_id: str,
    ref_item: dict[str, Any],
    *,
    scope: RefScope = "plan",
    plan_context: dict[str, Any],
    backend: ReferenceDecompileBackend | None = None,
    on_step: RefStepCallback | None = None,
) -> dict[str, Any]:
    """Download Modrinth jar, decompile+remap, build index metadata."""
    import asyncio

    from plan.reference_index import build_index, ref_project_dir, save_index

    project_id = ref_item.get("project_id") or ref_item.get("slug") or "unknown"
    title = ref_item.get("title") or project_id
    mc_version = (plan_context.get("minecraft_version") or "1.20.1").strip()
    loader = (plan_context.get("mod_loader") or "fabric").strip().lower()
    base = ref_project_dir(user_id, scope_id, project_id, scope=scope)
    ensure_dir(base)
    artifact_dir = base / "artifact"
    ensure_dir(artifact_dir)
    jar_path = artifact_dir / "mod.jar"
    work_dir = base / "_decompile_work"
    repo = base / "repo"

    if loader != "fabric":
        raise DecompileError(f"闭源反编译首期仅支持 Fabric，当前 loader={loader}")

    backend = backend or get_decompile_backend()
    global _current_decompile_log
    _current_decompile_log = base / "decompile.log"
    if _current_decompile_log.exists():
        _current_decompile_log.unlink(missing_ok=True)
    _append_decompile_log(f"decompile start project={project_id} mc={mc_version}\n")

    try:
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="ensure_tools",
            label="准备反编译工具",
            status="running",
        )
        if not shutil.which("java"):
            await _step(
                on_step,
                project_id=project_id,
                title=title,
                step="ensure_tools",
                label="准备反编译工具",
                status="failed",
                preview="未找到 Java",
            )
            raise DecompileError("未找到 Java 17+ 运行时", failure_kind="missing_java")

        tool_st = await asyncio.to_thread(ensure_decompile_tools, auto_download=True)
        if not (tool_st.vineflower_ok and tool_st.tiny_remapper_ok):
            preview = tool_st.error or "vineflower / tiny-remapper 不可用"
            await _step(
                on_step,
                project_id=project_id,
                title=title,
                step="ensure_tools",
                label="准备反编译工具",
                status="failed",
                preview=preview[:200],
            )
            raise DecompileError(preview, failure_kind="missing_tools")
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="ensure_tools",
            label="准备反编译工具",
            status="ok",
            preview=f"Vineflower {tool_st.vineflower_version} · tiny-remapper {tool_st.tiny_remapper_version}",
        )

        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="modrinth_resolve",
            label="解析 Modrinth 版本",
            status="running",
            preview=f"{mc_version} · {loader}",
        )
        version = await fetch_matching_version(project_id, game_version=mc_version, loader=loader)
        jar_file = pick_primary_jar_file(version)
        ver_label = version.get("version_number") or version.get("id") or ""
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="modrinth_resolve",
            label="解析 Modrinth 版本",
            status="ok",
            preview=ver_label,
        )

        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="modrinth_download",
            label="下载 Modrinth jar",
            status="running",
            preview=jar_file.get("filename") or "mod.jar",
        )
        await download_version_file(jar_file, jar_path)
        size_mb = jar_path.stat().st_size / (1024 * 1024)
        class_n = _jar_class_count(jar_path)
        logger.info(
            "downloaded jar %s size=%.2fMB classes=%s path=%s",
            jar_file.get("filename"),
            size_mb,
            class_n,
            jar_path,
        )
        _append_decompile_log(f"jar downloaded: {size_mb:.2f}MB, {class_n} classes\n")
        if size_mb > MAX_ARTIFACT_MB:
            raise DecompileError(
                f"jar 体积 {size_mb:.1f}MB 超过上限 {MAX_ARTIFACT_MB}MB",
                failure_kind="decompile_error",
            )
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="modrinth_download",
            label="下载 Modrinth jar",
            status="ok",
            preview=f"{jar_file.get('filename') or 'mod.jar'} · {size_mb:.1f}MB",
        )

        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="yarn_fetch",
            label="获取 Yarn 映射",
            status="running",
        )
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="tiny_remapper",
            label="Yarn 重映射 jar",
            status="running",
        )
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="vineflower",
            label="Vineflower 反编译",
            status="running",
        )

        result = await asyncio.to_thread(
            backend.decompile_and_remap,
            jar_path,
            repo,
            mc_version=mc_version,
            work_dir=work_dir,
        )
        result.version_id = version.get("id") or version.get("version_number")

        yarn_preview = result.yarn_build or mc_version
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="yarn_fetch",
            label="获取 Yarn 映射",
            status="ok",
            preview=yarn_preview,
        )
        remap_status = "ok" if result.source_kind == "decompiled" else "skipped"
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="tiny_remapper",
            label="Yarn 重映射 jar",
            status=remap_status,
            preview=result.mapping or "降级为混淆反编译",
        )
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="vineflower",
            label="Vineflower 反编译",
            status="ok",
            preview=result.source_kind,
        )

        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="build_index",
            label="构建源码索引",
            status="running",
        )
        index = await asyncio.to_thread(build_index, repo)
        file_count = index.get("file_count") or 0
        if file_count == 0:
            java_n = _count_java_files(repo)
            raise DecompileError(
                f"反编译未产生可索引源码（0 个文件，repo 内 {java_n} 个 .java）",
                failure_kind="decompile_error",
            )
        save_index(user_id, scope_id, project_id, index, scope=scope)
        logger.info(
            "index built project=%s file_count=%s entry_points=%s",
            project_id,
            file_count,
            len(index.get("entry_points") or []),
        )
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step="build_index",
            label="构建源码索引",
            status="ok",
            preview=f"{file_count} 个文件",
        )
        write_json(
            base / "decompile_meta.json",
            {
                "source_kind": result.source_kind,
                "mapping": result.mapping,
                "yarn_build": result.yarn_build,
                "version_id": result.version_id,
                "game_version": mc_version,
                "loader": loader,
                "degrade_reason": result.degrade_reason,
            },
        )

        return {
            "project_id": project_id,
            "status": "ready",
            "source_kind": result.source_kind,
            "mapping": result.mapping,
            "source_url": "",
            "error": None,
            "title": title,
            "slug": ref_item.get("slug") or project_id,
            "entry_points": index.get("entry_points") or [],
            "key_files": index.get("key_files") or [],
            "file_count": index.get("file_count") or 0,
            "version_id": result.version_id,
            "game_version": mc_version,
            "loader": loader,
            "degrade_reason": result.degrade_reason,
            "agent_fallback": False,
        }
    except ModrinthError as exc:
        raise DecompileError(str(exc), failure_kind="decompile_error") from exc
    except DecompileError as exc:
        fail_step = "vineflower"
        if "Modrinth" in str(exc) or "版本" in str(exc):
            fail_step = "modrinth_resolve"
        elif "下载" in str(exc):
            fail_step = "modrinth_download"
        elif exc.failure_kind == "missing_tools":
            fail_step = "ensure_tools"
        elif exc.failure_kind == "missing_java":
            fail_step = "ensure_tools"
        elif "0 个文件" in str(exc):
            fail_step = "build_index"
        await _step(
            on_step,
            project_id=project_id,
            title=title,
            step=fail_step,
            label=str(exc)[:120],
            status="failed",
            preview=str(exc)[:200],
        )
        raise
    finally:
        _current_decompile_log = None
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)

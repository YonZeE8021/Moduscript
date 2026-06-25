"""Fabric 1.20.1 template bootstrap."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

from config import (
    MOD_TEMPLATE_DOWNLOAD_TIMEOUT_MS,
    MOD_TEMPLATE_MINECRAFT_VERSION,
    MOD_TEMPLATE_PACKAGE,
    MOD_TEMPLATE_PAGE_TIMEOUT_MS,
    MOD_TEMPLATE_URL,
)

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_URL = MOD_TEMPLATE_URL

ProgressCallback = Callable[[str, str, str | None], None]


def _emit_progress(
    on_progress: ProgressCallback | None,
    step_id: str,
    status: str,
    detail: str | None = None,
) -> None:
    if on_progress:
        on_progress(step_id, status, detail)


def has_gradlew(path: Path) -> bool:
    return (path / "gradlew").is_file() or (path / "gradlew.bat").is_file()


def _has_gradlew(path: Path) -> bool:
    return has_gradlew(path)


def normalize_mod_name(user_input: str) -> tuple[str, str]:
    mod_name = user_input.strip() or "moduscript"
    return mod_name.lower().replace(" ", "_")[:32], mod_name[:40]


def _locate_project_dir(target_folder: Path) -> Path | None:
    if _has_gradlew(target_folder):
        return target_folder
    subdirs = [d for d in target_folder.iterdir() if d.is_dir() and not d.name.startswith("__")]
    if len(subdirs) == 1 and _has_gradlew(subdirs[0]):
        return subdirs[0]
    return None


def _collect_page_diagnostics(page: Any, work_dir: Path, console_errors: list[str]) -> dict[str, Any]:
    screenshot = work_dir / "mod_template_debug.png"
    try:
        page.screenshot(path=str(screenshot))
    except Exception as exc:
        logger.warning("Failed to capture bootstrap debug screenshot: %s", exc)

    download_button = page.locator("a.download-button")
    version_select = page.locator("#minecraft-version")
    version_count = 0
    try:
        version_count = version_select.locator("option").count()
    except Exception:
        pass

    download_disabled = None
    try:
        if download_button.count() > 0:
            download_disabled = download_button.first.is_disabled()
    except Exception:
        pass

    body_snippet = ""
    try:
        body_snippet = (page.locator("body").inner_text(timeout=2000) or "")[:400]
    except Exception:
        pass

    return {
        "url": page.url,
        "version_option_count": version_count,
        "download_button_disabled": download_disabled,
        "body_snippet": body_snippet,
        "console_errors": console_errors[-8:],
        "screenshot": str(screenshot),
    }


def _format_bootstrap_error(message: str, diagnostics: dict[str, Any]) -> str:
    detail = (
        f"url={diagnostics.get('url')}; "
        f"version_options={diagnostics.get('version_option_count')}; "
        f"download_disabled={diagnostics.get('download_button_disabled')}"
    )
    console_errors = diagnostics.get("console_errors") or []
    if console_errors:
        detail += f"; console={' | '.join(console_errors[:2])}"
    return f"{message} ({detail})"


def _wait_for_template_page_ready(page: Any, *, page_timeout_ms: int) -> None:
    page.locator("a", has_text="Use custom id").click(timeout=page_timeout_ms)

    for selector in ("#mod-id", "#package-name", "#minecraft-version"):
        page.locator(selector).wait_for(state="visible", timeout=page_timeout_ms)

    page.wait_for_function(
        """() => {
            const select = document.querySelector('#minecraft-version');
            return select && select.options && select.options.length > 1;
        }""",
        timeout=page_timeout_ms,
    )

    page.wait_for_function(
        """() => {
            const text = (document.body?.innerText || '').toLowerCase();
            return !text.includes('loading...');
        }""",
        timeout=page_timeout_ms,
    )


def download_template_zip(
    mod_id: str,
    mod_name: str,
    work_dir: Path,
    *,
    package_name: str,
    minecraft_version: str,
    template_url: str,
    page_timeout_ms: int = MOD_TEMPLATE_PAGE_TIMEOUT_MS,
    download_timeout_ms: int = MOD_TEMPLATE_DOWNLOAD_TIMEOUT_MS,
    on_progress: ProgressCallback | None = None,
) -> Path:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    work_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        diagnostics: dict[str, Any] = {}
        try:
            _emit_progress(on_progress, "connect", "active", "正在连接 fabricmc.net…")
            page.goto(template_url, wait_until="domcontentloaded", timeout=page_timeout_ms)
            _wait_for_template_page_ready(page, page_timeout_ms=page_timeout_ms)
            _emit_progress(on_progress, "connect", "done", "已连接 Fabric 模板服务")

            _emit_progress(on_progress, "download", "active", "正在填写模板参数…")
            page.locator("#mod-id").fill(mod_id)
            mod_name_input = page.locator("#project-name")
            if mod_name_input.is_visible():
                mod_name_input.fill(mod_name)
            page.locator("#package-name").fill(package_name)
            page.locator("#minecraft-version").select_option(minecraft_version)

            checkboxes = page.locator("input.option-input[type=checkbox]")
            for i in range(checkboxes.count()):
                cb = checkboxes.nth(i)
                if cb.get_attribute("id") == "datagen":
                    cb.check()
                elif cb.is_checked():
                    cb.uncheck()

            page.wait_for_timeout(800)

            download_button = page.locator("a.download-button")
            download_button.wait_for(state="visible", timeout=page_timeout_ms)
            if download_button.is_disabled():
                diagnostics = _collect_page_diagnostics(page, work_dir, console_errors)
                _emit_progress(on_progress, "download", "failed", "下载按钮暂不可用")
                raise RuntimeError(
                    _format_bootstrap_error("Fabric 模板下载按钮不可用", diagnostics)
                )

            with page.expect_download(timeout=download_timeout_ms) as download_info:
                download_button.click()
            download = download_info.value
            zip_path = work_dir / (download.suggested_filename or "fabric-template.zip")
            download.save_as(str(zip_path))
            _emit_progress(on_progress, "download", "done", zip_path.name)
            return zip_path
        except PlaywrightTimeoutError as exc:
            diagnostics = _collect_page_diagnostics(page, work_dir, console_errors)
            if diagnostics.get("version_option_count", 0) <= 1:
                message = "Fabric 模板页版本列表未加载完成"
            elif diagnostics.get("download_button_disabled"):
                message = "Fabric 模板下载按钮不可用"
            else:
                message = "Fabric 模板下载超时"
            debug_json = work_dir / "mod_template_debug.json"
            debug_json.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            failed_step = "download"
            if diagnostics.get("version_option_count", 0) <= 1:
                failed_step = "connect"
            _emit_progress(on_progress, failed_step, "failed", message)
            raise RuntimeError(_format_bootstrap_error(message, diagnostics)) from exc
        except Exception:
            if not diagnostics:
                diagnostics = _collect_page_diagnostics(page, work_dir, console_errors)
                debug_json = work_dir / "mod_template_debug.json"
                debug_json.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            _emit_progress(on_progress, "download", "failed", "模板获取失败")
            raise
        finally:
            browser.close()


def bootstrap_fabric_workspace(
    workspace: Path,
    mod_name: str,
    *,
    mod_id: str | None = None,
    package_name: str | None = None,
    minecraft_version: str | None = None,
    template_url: str | None = None,
    run_build: bool = False,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """
    若 workspace 尚无 gradlew，下载 Fabric 模板并解压到 workspace。
    返回包含 gradlew 的项目目录。
    """
    workspace = workspace.resolve()
    if _has_gradlew(workspace):
        return workspace

    located = _locate_project_dir(workspace)
    if located:
        return located

    mod_id_norm, normalized = normalize_mod_name(mod_name)
    if mod_id and mod_id.strip():
        mod_id_norm = mod_id.strip().lower().replace(" ", "_")[:32]
    if mod_name.strip():
        normalized = mod_name.strip()[:40]
    pkg = package_name or MOD_TEMPLATE_PACKAGE
    mc = minecraft_version or MOD_TEMPLATE_MINECRAFT_VERSION
    url = template_url or MOD_TEMPLATE_URL

    staging = workspace / "_bootstrap"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        zip_path = download_template_zip(
            mod_id_norm,
            normalized,
            staging,
            package_name=pkg,
            minecraft_version=mc,
            template_url=url,
            on_progress=on_progress,
        )

        _emit_progress(on_progress, "build", "active", "正在解压模板到工作区…")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(staging)
        zip_path.unlink(missing_ok=True)

        project_dir = _locate_project_dir(staging)
        if project_dir is None:
            _emit_progress(on_progress, "build", "failed", "无法定位项目目录")
            raise RuntimeError("无法定位 Fabric 模板项目目录")

        for item in project_dir.iterdir():
            dest = workspace / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))
        _emit_progress(on_progress, "build", "done", "Gradle 项目已就绪")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    else:
        shutil.rmtree(staging, ignore_errors=True)

    if run_build:
        import subprocess

        _emit_progress(on_progress, "build", "active", "正在执行 gradlew build…")
        gradlew_cmd = "gradlew.bat" if sys.platform == "win32" else "./gradlew"
        subprocess.run(
            [gradlew_cmd, "build"],
            cwd=str(workspace),
            check=False,
            shell=sys.platform == "win32",
        )
        _emit_progress(on_progress, "build", "done", "构建命令已执行")

    result = _locate_project_dir(workspace) or workspace
    if not _has_gradlew(result):
        raise RuntimeError("Fabric 模板解压后未找到 gradlew")
    logger.info("Fabric workspace ready: %s", result)
    return result

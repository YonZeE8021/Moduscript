"""Validate Fabric mod display name, mod id, and Java package name."""

from __future__ import annotations

import re
from dataclasses import dataclass

MOD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &'.\-]{0,39}$")
MOD_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")
PACKAGE_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
INVALID_MOD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    message: str = ""


def normalize_mod_id(mod_id: str) -> str:
    """Slugify user input into a Fabric-safe mod id (2–32 chars, lowercase)."""
    s = (mod_id or "").strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or not s[0].isalpha():
        s = f"mod_{s}" if s else "moduscript"
        s = re.sub(r"^mod_+", "mod_", s)
    if len(s) < 2:
        s = (s + "mod")[:32]
    return s[:32]


def validate_mod_name(name: str) -> ValidationResult:
    value = (name or "").strip()
    if not value:
        return ValidationResult(True)
    if len(value) < 2:
        return ValidationResult(False, "Mod 名称至少 2 个字符")
    if len(value) > 40:
        return ValidationResult(False, "Mod 名称最多 40 个字符")
    if INVALID_MOD_NAME_CHARS.search(value):
        return ValidationResult(False, 'Mod 名称不能包含 \\ / : * ? " < > |')
    if re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", value):
        return ValidationResult(
            False,
            "Mod 名称须为英文（拉丁字母、数字及 & . ' - 空格），请留空以自动生成",
        )
    if not MOD_NAME_RE.fullmatch(value):
        return ValidationResult(
            False,
            "Mod 名称仅允许拉丁字母、数字、空格及 & . ' -，且须以字母或数字开头",
        )
    return ValidationResult(True)


def validate_mod_id(mod_id: str) -> ValidationResult:
    raw = (mod_id or "").strip()
    if not raw:
        return ValidationResult(True)
    if len(raw) < 2:
        return ValidationResult(False, "Mod ID 至少 2 个字符")
    if len(raw) > 32:
        return ValidationResult(False, "Mod ID 最多 32 个字符")
    if re.search(r"[A-Z]", raw):
        return ValidationResult(False, "Mod ID 不允许大写字母")
    if " " in raw:
        return ValidationResult(False, "Mod ID 不能包含空格，请使用下划线")
    if not MOD_ID_RE.fullmatch(raw):
        return ValidationResult(
            False,
            "Mod ID 仅含小写字母、数字与下划线（如 my_mod）",
        )
    return ValidationResult(True)


def validate_package_name(package_name: str) -> ValidationResult:
    value = (package_name or "").strip().lower()
    if not value:
        return ValidationResult(True)
    if len(value) > 80:
        return ValidationResult(False, "包名最多 80 个字符")
    if value != (package_name or "").strip():
        return ValidationResult(False, "包名须为小写")
    if " " in package_name:
        return ValidationResult(False, "包名不能包含空格")
    segments = value.split(".")
    if len(segments) < 2:
        return ValidationResult(False, "包名至少包含两段，如 com.example.mymod")
    for seg in segments:
        if not seg:
            return ValidationResult(False, "包名不能包含连续的点或首尾点")
        if not PACKAGE_SEGMENT_RE.fullmatch(seg):
            return ValidationResult(
                False,
                "包名每段须以小写字母开头，仅含小写字母、数字与下划线",
            )
    return ValidationResult(True)


def validate_mod_metadata_fields(
    *,
    mod_name: str | None = None,
    mod_id: str | None = None,
    package_name: str | None = None,
) -> ValidationResult:
    for check in (
        validate_mod_name(mod_name or ""),
        validate_mod_id(mod_id or ""),
        validate_package_name(package_name or ""),
    ):
        if not check.valid:
            return check
    return ValidationResult(True)

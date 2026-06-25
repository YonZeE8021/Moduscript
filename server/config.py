"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("MCMOD_DATA_DIR", str(ROOT_DIR / "data"))).resolve()

JWT_SECRET = os.getenv("JWT_SECRET", "dev-change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

BOOTSTRAP_ADMIN_EMAIL = (os.getenv("MCMOD_BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
ADMIN_PASSWORD = (os.getenv("MCMOD_ADMIN_PASSWORD") or "").strip()

USE_MOCK_SESSIONS = os.getenv("USE_MOCK_SESSIONS", "false").lower() in ("1", "true", "yes")

WORKSPACE_ROOT = DATA_DIR / "workspaces"
USERS_DIR = DATA_DIR / "users"
ADMIN_DIR = DATA_DIR / "admin"

CHAT_MAX_TURNS = int(os.getenv("CHAT_MAX_TURNS", "150"))
CHAT_MAX_TURNS_MIN = int(os.getenv("CHAT_MAX_TURNS_MIN", "10"))
CHAT_MAX_TURNS_MAX = int(os.getenv("CHAT_MAX_TURNS_MAX", "500"))
CHAT_THINKING_DEFAULT = (os.getenv("CHAT_THINKING_DEFAULT", "adaptive") or "adaptive").strip().lower()
CHAT_EFFORT_DEFAULT = (
    os.getenv("CLAUDE_CODE_EFFORT_LEVEL") or os.getenv("CHAT_EFFORT_DEFAULT", "high") or "high"
).strip().lower()

MOD_TEMPLATE_PACKAGE = (os.getenv("MOD_TEMPLATE_PACKAGE") or "com.example").strip()
MOD_TEMPLATE_MINECRAFT_VERSION = (os.getenv("MOD_TEMPLATE_MINECRAFT_VERSION") or "1.20.1").strip()
MOD_TEMPLATE_URL = (os.getenv("MOD_TEMPLATE_URL") or "https://fabricmc.net/develop/template/").strip()
MOD_TEMPLATE_PAGE_TIMEOUT_MS = int(os.getenv("MOD_TEMPLATE_PAGE_TIMEOUT_MS", "120000"))
MOD_TEMPLATE_DOWNLOAD_TIMEOUT_MS = int(os.getenv("MOD_TEMPLATE_DOWNLOAD_TIMEOUT_MS", "120000"))
MOD_TEMPLATE_MAX_RETRIES = int(os.getenv("MOD_TEMPLATE_MAX_RETRIES", "3"))
MOD_TEMPLATE_RETRY_DELAY_SEC = float(os.getenv("MOD_TEMPLATE_RETRY_DELAY_SEC", "3"))
GRADLE_BUILD_TIMEOUT_SEC = int(os.getenv("GRADLE_BUILD_TIMEOUT_SEC", "600"))

LOG_DIR = Path(os.getenv("LOG_DIR", str(DATA_DIR / "logs"))).resolve()

SSE_PUBLISH_DEBOUNCE_MS = int(os.getenv("SSE_PUBLISH_DEBOUNCE_MS", "300"))
SSE_STRIP_HEAVY_FIELDS = os.getenv("SSE_STRIP_HEAVY_FIELDS", "true").lower() in ("1", "true", "yes")

AGENT_TRACE_ENABLED = os.getenv("AGENT_TRACE_ENABLED", "true").lower() in ("1", "true", "yes")
AGENT_TRACE_PATH = Path(os.getenv("AGENT_TRACE_PATH", str(LOG_DIR / "agent_trace.jsonl"))).resolve()
AGENT_TRACE_PREVIEW_LEN = int(os.getenv("AGENT_TRACE_PREVIEW_LEN", "500"))
AGENT_IDLE_WARN_SEC = int(os.getenv("AGENT_IDLE_WARN_SEC", "120"))
AGENT_TRACE_LOG_DELTAS = os.getenv("AGENT_TRACE_LOG_DELTAS", "false").lower() in ("1", "true", "yes")

SUPPORTED_MC_VERSION = "1.20.1"
SUPPORTED_MOD_LOADER = "fabric"

DEEPSEEK_API_KEY = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
DEEPSEEK_BASE_URL = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
DEEPSEEK_OPTIMIZE_MODEL = (os.getenv("DEEPSEEK_OPTIMIZE_MODEL") or "deepseek-v4-pro").strip()
DEEPSEEK_OPTIMIZE_REASONING_EFFORT = (
    os.getenv("DEEPSEEK_OPTIMIZE_REASONING_EFFORT") or "high"
).strip().lower()

LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
AUDIT_JSONL_ENABLED = os.getenv("AUDIT_JSONL_ENABLED", "true").lower() in ("1", "true", "yes")
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "30"))
UVICORN_ACCESS_LOG = os.getenv("UVICORN_ACCESS_LOG", "false").lower() in ("1", "true", "yes")
UVICORN_GRACEFUL_TIMEOUT = int(os.getenv("UVICORN_GRACEFUL_TIMEOUT", "30"))

# 0 = unlimited (dev/tests). Production A-test: 8–12 recommended.
SESSION_MAX_ACTIVE = int(os.getenv("SESSION_MAX_ACTIVE", "0"))
# 0 = unlimited. Production: 2 recommended.
GRADLE_MAX_CONCURRENT = int(os.getenv("GRADLE_MAX_CONCURRENT", "0"))

_cors_raw = (os.getenv("CORS_ORIGINS") or "").strip()
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()] if _cors_raw else ["*"]

JWT_SECRET_WEAK_DEFAULT = "dev-change-me-in-production"
REQUIRE_STRONG_SECRETS = os.getenv("MCMOD_REQUIRE_STRONG_SECRETS", "false").lower() in (
    "1",
    "true",
    "yes",
)

"""Smoke check: required Python packages are importable."""
import sys

mods = ("fastapi", "uvicorn", "playwright", "claude_agent_sdk")
missing = []
for name in mods:
    try:
        __import__(name)
    except ImportError:
        missing.append(name)

if missing:
    print("IMPORT_FAIL:" + ",".join(missing))
    sys.exit(1)
print("IMPORT_OK")

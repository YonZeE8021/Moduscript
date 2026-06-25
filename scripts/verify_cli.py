"""Smoke check: Claude CLI path and Windows asyncio subprocess."""
import asyncio
import subprocess
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent.options import resolve_cli_path, verify_cli_async_transport


def main() -> int:
    try:
        cli = resolve_cli_path()
        proc = subprocess.run([cli, "-v"], capture_output=True, text=True, timeout=30)
        version = (proc.stdout or proc.stderr or "").strip().replace("\n", " ")
        if proc.returncode != 0:
            print("CLI_FAIL:" + (version or f"exit {proc.returncode}"))
        else:
            print(f"CLI_OK:{cli}:{version}")
    except Exception as exc:
        print("CLI_FAIL:" + str(exc))

    async def _check_async() -> None:
        ok, msg = await verify_cli_async_transport()
        if ok:
            print("ASYNC_OK")
        else:
            print("ASYNC_WARN:" + msg)

    asyncio.run(_check_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

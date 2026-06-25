#!/usr/bin/env python3
"""Unified deploy CLI — receive / deploy / stop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY_DIR))

from lib.paths import init_paths  # noqa: E402

init_paths(DEPLOY_DIR)

from lib.cli_util import (  # noqa: E402
    run_main,
    start_receiver_daemon,
    start_receiver_foreground,
    stop_receiver,
)
from sender import main as sender_main  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy/cli.py",
        description="MCmodAgent deploy tools",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    recv = sub.add_parser("receive", help="Start deploy receiver (foreground, default)")
    recv.add_argument(
        "--daemon",
        action="store_true",
        help="Run in background and write logs to deploy/receiver.log",
    )

    sub.add_parser("stop", help="Stop background receiver")

    send = sub.add_parser("deploy", help="Push updates to remote receiver")
    send.add_argument("--force", action="store_true", help="Force full sync")
    send.add_argument("--dry-run", action="store_true", help="List files only")
    send.add_argument(
        "--config",
        default=str(DEPLOY_DIR / "config" / "sender.json"),
        help="Path to sender.json",
    )
    send.add_argument(
        "--psk",
        default=str(DEPLOY_DIR / "keys" / "psk.hex"),
        help="Path to PSK hex file",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "receive":
        if args.daemon:
            return start_receiver_daemon()
        return start_receiver_foreground()

    if args.command == "stop":
        return stop_receiver()

    if args.command == "deploy":
        deploy_argv: list[str] = []
        if args.force:
            deploy_argv.append("--force")
        if args.dry_run:
            deploy_argv.append("--dry-run")
        deploy_argv.extend(["--config", args.config, "--psk", args.psk])
        return sender_main(deploy_argv)

    return 1


if __name__ == "__main__":
    run_main(lambda: main())

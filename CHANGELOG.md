# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Initial public open-source release under GNU AGPL-3.0.
- Moduscript platform: task configuration, Agent writing workspace, plan mode, session branching.
- FastAPI backend with Claude Agent SDK integration.
- Admin panel, JWT auth, Modrinth reference mods, closed-source mod decompile reference (plan mode).
- TCP deploy tooling (`deploy/`) with MDPL protocol.
- One-click public snapshot publish script (`scripts/publish-public.ps1`).

### Changed

- Default mod template package namespace: `com.example` (configurable via `MOD_TEMPLATE_PACKAGE`).
- Deploy secrets moved to local-only files with `.example` templates.

### Removed

- Agent debug instrumentation and accidental decompiler output directories.
- Orphan `mock-session-backend.js` and internal marketing assets from repository.

## [0.1.0] - TBD

First tagged release after public repository publish.

[Unreleased]: https://github.com/YOUR_ORG/MCmodAgent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/YOUR_ORG/MCmodAgent/releases/tag/v0.1.0

# Third-Party Licenses

This document lists notable third-party software used by Moduscript (MCmodAgent).
See [NOTICE](NOTICE) for project copyright.

## Runtime dependencies (Python)

| Package | License | Notes |
|---------|---------|-------|
| [FastAPI](https://github.com/tiangolo/fastapi) | MIT | Web API framework |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI server |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | HTTP client |
| [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python) | MIT | Claude Agent SDK; usage subject to [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms) |
| [python-jose](https://github.com/mpdavis/python-jose) | MIT | JWT |
| [bcrypt](https://github.com/pyca/bcrypt) | Apache-2.0 | Password hashing |
| [Playwright](https://github.com/microsoft/playwright-python) | Apache-2.0 | Browser automation; bundles Chromium under its own licenses |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | Apache-2.0 | DeepSeek-compatible API client |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | Environment loading |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | Form uploads |

## Downloaded at runtime (plan reference decompile)

| Component | Version (in code) | License | Source |
|-----------|-------------------|---------|--------|
| [Vineflower](https://github.com/Vineflower/vineflower) | 1.11.1 | Apache-2.0 | `server/plan/reference_config.py` |
| [tiny-remapper](https://github.com/FabricMC/tiny-remapper) | 0.10.4 | LGPL-3.0 | Downloaded to `data/tools/` on first use |

**LGPL-3.0 (tiny-remapper):** If you distribute a modified version of this software that invokes tiny-remapper, comply with LGPL-3.0 (provide corresponding source or replacement library per license terms).

## External services

| Service | Terms |
|---------|-------|
| Anthropic Claude API / Claude Code CLI | [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms) |
| Modrinth API | [Modrinth Terms](https://modrinth.com/legal/terms) |
| Fabric toolchain / templates | Fabric & Minecraft ecosystem licenses |

## Minecraft / Mojang

Minecraft is a trademark of Mojang AB. This project is **not** affiliated with Mojang or Microsoft.

## Reference mod decompilation

Plan mode may download and decompile third-party mod JARs for reference indexing. You are responsible for compliance with mod authors' licenses and platform terms. See [docs/LEGAL_NOTES.md](docs/LEGAL_NOTES.md).

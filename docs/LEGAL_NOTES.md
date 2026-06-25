# Legal Notes

This document supplements [LICENSE](../LICENSE) and [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md).

## Project license (AGPL-3.0)

Moduscript (MCmodAgent) is licensed under the **GNU Affero General Public License v3.0**.

If you run a **modified** version as a network service (users interact with it over a network), AGPL-3.0 requires you to offer corresponding source code to users. See [LICENSE](../LICENSE) Section 13.

## Anthropic Claude / Agent SDK

Real mod-writing sessions use the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python) and typically the Claude Code CLI. Your use is subject to [Anthropic's Commercial Terms](https://www.anthropic.com/legal/commercial-terms) and applicable API policies—not only the SDK's MIT license.

You must provide your own API credentials; do not use keys belonging to others.

## Reference mod indexing and decompilation

Plan mode (and session reference features) may:

- Clone **open-source** repositories from URLs you provide.
- Download mod JARs from **Modrinth** and decompile them (Vineflower + tiny-remapper) for indexing when `decompile_attempt` is enabled.

**You are responsible** for ensuring this use complies with:

- Mod authors' licenses and redistribution terms
- [Modrinth Terms of Use](https://modrinth.com/legal/terms)
- Applicable copyright law in your jurisdiction

Decompiled output is stored under per-user `data/` paths and is intended for **private reference** during planning—not for redistribution as your own mod.

## Minecraft / Mojang

This software generates Minecraft mod source code. Minecraft is a trademark of Mojang AB / Microsoft. Generated mods must comply with Mojang's [Commercial Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines) and [EULA](https://www.minecraft.net/en-us/eula) as applicable.

## Self-hosted vs official service

The open-source repository enables **self-hosting**. Any official closed-beta or hosted service operated by the project maintainers is separate; see [CLOSED_BETA.md](CLOSED_BETA.md) for beta-specific terms when applicable.

## Data storage

User accounts, sessions, and preferences are stored as JSON files under `data/`. This is convenient for development but is **not** a hardened production database. See [DEPLOYMENT.md](DEPLOYMENT.md) and [SECURITY.md](../SECURITY.md).

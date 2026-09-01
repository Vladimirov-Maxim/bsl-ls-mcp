# Third-party components

This project is an MCP wrapper. It **invokes** the following tool as a separate
process (over stdio / batch CLI) — it does not link against it, and does not
redistribute its binary. The jar is downloaded from the upstream release at
build time (see `build.ps1`).

- **BSL Language Server** — https://github.com/1c-syntax/bsl-language-server
  Licensed under **GNU LGPL v3.0**. Copyright © 1c-syntax contributors.
  The `bsl-language-server-*-exec.jar` used by this wrapper must be obtained
  from the upstream GitHub Releases page; it is **not** included in this
  repository.

Python runtime dependency:

- **mcp** (Model Context Protocol SDK) — MIT License.

Build-time / optional tooling (not required to run the wrapper from source):

- **PyInstaller** (bundling) — GPL v2 with a bundling runtime exception.
- **pystray**, **Pillow** (system-tray helper) — LGPL-3.0 / HPND respectively.
- **NSSM** (Windows service shim) — public domain; downloaded by
  `install-service.ps1`, not included here.

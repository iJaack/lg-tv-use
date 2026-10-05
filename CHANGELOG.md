# Changelog

## 0.3.1 — 2026-10-05

First public source release, under the MIT license.

- Curated source, mock tests and dependency lock; no device state, captures or SDK copies.
- Explicit TV address configuration; no implicit default target.
- Generic pairing identity and configurable `TV_STATE_DIR`; scoped addon storage.
- Installed `lg-tv-use` CLI and `lg-tv-use-mcp` stdio entry points.
- English setup, tool reference, architecture, compatibility, contribution and security docs.
- Configuration regression tests, real MCP schema smoke test and package validation CI.

## Private prototype history

0.3 introduced per-TV model/MAC registration, power verification, UI recipe learning
and idle pointer liveness checks. 0.2 introduced resident connections, native route
learning and generated addons. These prototypes were physically explored on two LG
models; their private evidence and state are intentionally not distributed.

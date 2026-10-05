# LG TV Use

[![CI](https://github.com/iJaack/lg-tv-use/actions/workflows/ci.yml/badge.svg)](https://github.com/iJaack/lg-tv-use/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A local MCP server and CLI for LG webOS TVs. Control apps, remote keys, pointer,
text and power over your LAN. Turn visually checked app operations into reusable
Python addons, so known routes can run without taking a screenshot at every step.

**Experimental, version 0.3.1.** This is an agent tool, not a continuously running
autonomous agent. An agent or operator must inspect new screens before learning
routes. Native foreground state and predicted UI state do not prove what is playing
or which control has focus.

## Features

- 17 MCP tools over stdio, plus a CLI and persistent JSON-lines session.
- Native app launch, advertised app search, LG browser URLs and YouTube video IDs.
- Bounded remote keys, relative pointer motion, clicks, scrolling and text input.
- Per-device pairing, model/MAC identity checks, power-off and Wake-on-LAN with
  native power-state readback.
- App/firmware-scoped route learning, generated local addons and visual fallback
  for unknown or stale routes.
- UI recipes with an exact observed starting page, focus, keyboard and modal state.
  Replays skip images only when that tracked context is still valid.
- No automatic replay of a command whose delivery is uncertain.

## Requirements

- Python 3.11+ on macOS or Linux. Windows is unsupported (`fcntl` file locking).
- An LG webOS TV with the second-screen service reachable on the same LAN.
- A private IPv4 address for the TV; multicast discovery is optional.
- Physical acceptance of the **LG TV Use** pairing prompt on each TV.
- For waking: supported network standby/mobile power-on settings and a suitable
  LAN broadcast address. This varies by model and network.

No LG cloud account, developer mode, root access or API key is required by this tool.
See [compatibility and validation](docs/validation.md) for tested behavior and limits.

## Install and pair

This release is distributed through GitHub; there is no project PyPI release.

```sh
git clone https://github.com/iJaack/lg-tv-use.git
cd lg-tv-use
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps .

# Optional: discover second-screen advertisements, without scanning the subnet.
.venv/bin/lg-tv-use discover

# Replace the example address with the one shown in your TV network settings.
export TV_HOST=192.168.1.100
.venv/bin/lg-tv-use pair
```

Accept **LG TV Use** on the TV within 45 seconds. `--host` overrides `TV_HOST`.
There is no implicit target address and ordinary commands never initiate pairing.
Optionally add `--model YOUR_EXACT_MODEL` to reject a different native model before
saving credentials or executing commands.

```sh
.venv/bin/lg-tv-use apps
.venv/bin/lg-tv-use --capture always observe
.venv/bin/lg-tv-use launch youtube.leanback.v4
.venv/bin/lg-tv-use search '10 minute full body stretch'
.venv/bin/lg-tv-use browser https://example.com
.venv/bin/lg-tv-use --capture never press BACK
```

## Connect an MCP client

Use the installed `lg-tv-use-mcp` executable, with `TV_HOST` set in the client
configuration. An absolute executable path avoids GUI clients' PATH differences.
Copy and edit [examples/mcp.json](examples/mcp.json) for clients using `mcpServers`.

For Codex:

```sh
codex mcp add lg-tv-use --env TV_HOST=192.168.1.100 -- /absolute/path/to/lg-tv-use/.venv/bin/lg-tv-use-mcp
```

Restart or reload an already running MCP session after configuration or schema
changes. Registering the server and loading its tools are separate steps. The
server opens no inbound HTTP port on the host.

Example requests to your agent:

- “Show me what is on the TV.”
- “Open YouTube and search for a ten-minute stretching routine.”
- “Inspect this app's search screen and save a reusable route.”

Full tool arguments, capture policies and examples are in [the tool reference](docs/tools.md).

## Learning routes

For a native operation, run `tv_app_action` with `capture="auto"`. A new route
captures a discovery image. After inspecting the result, call `tv_learn` with its
witness ID and notes describing what the image actually proves. If the first image
shows loading, wait and use `tv_observe(capture="always")` before learning.

Subsequent matching operations use a generated wrapper and lightweight native
readback, without images. Changed app metadata, app version, TV or available
firmware scope requires discovery again.

For internal UI routes:

1. Inspect `tv_observe(capture="always")` and name its exact starting state with
   `tv_ui_anchor`. Include the underlying page, focused control, keyboard and modals.
2. Use `tv_ui_record` for a bounded sequence of keys/text/waits and a final image.
3. Inspect the final image. Learn only a result matching the declared destination.
4. Use `tv_ui_run` to replay from the same tracked origin. Unknown or expired
   context returns a screenshot without executing the recipe.

The witness must be at most 30 seconds old when anchoring. Context expires after
60 seconds and clears on untracked commands, reconnects, new images or TV changes.
External remote input cannot always be detected: observe again after anyone uses
the physical remote. `tracked_state` is a prediction, not a UI accessibility tree.
See [architecture](docs/architecture.md) for the generation and verification model.

## Multiple TVs and power

Pair and register each TV while it is on:

```sh
.venv/bin/lg-tv-use --host 192.168.1.100 register 'Living Room' --broadcast 192.168.1.255
.venv/bin/lg-tv-use --host 192.168.1.101 pair
.venv/bin/lg-tv-use --host 192.168.1.101 register 'Bedroom' --broadcast 192.168.1.255
.venv/bin/lg-tv-use --host 192.168.1.101 power status
.venv/bin/lg-tv-use --host 192.168.1.101 power off
.venv/bin/lg-tv-use --host 192.168.1.101 power on
```

Choose a broadcast appropriate for your own subnet. Registration stores native
network MACs and a stable device credential copy. In MCP, use `tv_devices`,
`tv_select(device="Bedroom")` and `tv_power(operation="on")`.

`power off` sends the native command once. `power on` sends bounded Wake-on-LAN
packets and may also use the native wake handshake when standby remains reachable.
Both attempt a native state readback. UDP delivery is not an acknowledgement and
an unreachable endpoint alone is **not proof of power-off**. If wake cannot be
verified, inspect the TV and its standby/network settings.

## Configuration and privacy

| Setting | Purpose |
| --- | --- |
| `TV_HOST` / `--host` | Required RFC1918 IPv4 TV address. |
| `TV_MODEL` / `--model` | Optional expected native model. |
| `TV_STATE_DIR` | Credentials/state root. Default: `~/Library/Application Support/lg-tv-use` on supported systems. |
| `TV_PROFILE_DIR` | Optional addons directory; registry lives there too, credentials remain in `TV_STATE_DIR`. |

Credential files use mode 0600; the credential directory uses 0700. Generated
addons and registry writes are atomic and use local file locks. CLI screenshots
are written to `outputs/` in the current directory with mode 0600. MCP images are
returned to the calling client: that client or its model provider may receive them.
Credentials, profiles, device identifiers and captures should never be committed.

Control sockets use TLS certificate pinning after explicit first pairing; they
do not fall back to plaintext WebSockets. Some TVs return screenshot URLs over
HTTP, so image transport can be unencrypted on the LAN. Downloads are restricted
to the selected TV, with redirects refused and a size cap. See [SECURITY.md](SECURITY.md).

## Troubleshooting

- **No TV discovered:** check its IP directly; multicast advertisements can be
  absent, stale or blocked. Only private IPv4 targets are supported.
- **Pairing rejected/timed out:** keep the TV on and accept its prompt; the tool
  does not treat missing acceptance as a successful connection.
- **Certificate changed:** verify the physical TV before removing only its stale
  credential file and pairing again. `pair` does not silently replace a pinned certificate.
- **Text ignored in YouTube:** use native `search`; generic LG IME is app-dependent.
- **Screenshot missing/black:** firmware and protected video may prevent capture.
- **Unexpected UI:** observe again; a known app launch does not prove a known screen.
- **TV won't wake:** check network standby/mobile power-on support and broadcast;
  Wi-Fi wake is not guaranteed on every model.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q tv_use.py tv_runtime.py tv_power.py app_learning.py tv_cli.py server.py tests
```

Tests use mocks and a temporary-state MCP subprocess; they send no TV commands.
CI checks Python 3.11/3.14 on macOS/Linux and builds/installs the package. Physical
TV acceptance remains a separate check. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[CHANGELOG.md](CHANGELOG.md).

## License and acknowledgements

MIT, copyright Jaack. Independent project, not affiliated with LG or the app vendors.
The webOS second-screen protocol was studied through
[LG Connect SDK](https://github.com/ConnectSDK/Connect-SDK-Android-Core).
Downloaded SDK sources and private device evidence are not included in this repository.
Runtime dependencies retain their own licenses.

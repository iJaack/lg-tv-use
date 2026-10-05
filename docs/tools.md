# Tool and CLI reference

The MCP server exposes these 17 tools. App IDs come from `tv_apps`, rather than
guessing vendor-specific identifiers. All TV tools require explicit prior pairing.

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `tv_discover` | none | SSDP second-screen discovery; no subnet sweep. |
| `tv_observe` | `capture="auto"` | Native foreground/audio state; optional image and visual witness. |
| `tv_apps` | none | App versions, learned routes, discovery requirements and launch evidence. |
| `tv_devices` | none | Registered device names/IDs, models and address conflicts. |
| `tv_select` | `device` | Select one registered name/ID and verify native identity. |
| `tv_power` | `operation`, `wait_seconds=20` | `on`, `off`, `status`; verification window 1–45 seconds. |
| `tv_app_action` | `app_id`, `operation="launch"`, `value=""`, `capture="auto"` | `launch`, `search`, `open_url`, `youtube_video`. |
| `tv_launch` | `app_id`, `capture="auto"` | App launch shorthand; store stubs are refused. |
| `tv_learn` | `witness_id`, `notes` | Learn the latest inspected action/recording result; notes up to 500 characters. |
| `tv_ui_anchor` | `witness_id`, `state` | Anchor an inspected screenshot from the last 30 seconds. |
| `tv_ui_record` | `name`, `from_state`, `to_state`, `steps`, `value=""` | Execute bounded UI recipe from an inspected origin; capture result once. |
| `tv_ui_run` | `app_id`, `name`, `value=""` | Generated replay from exact tracked origin; otherwise image fallback without execution. |
| `tv_press` | `button`, `capture="auto"` | `HOME`, `BACK`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `ENTER`. |
| `tv_move` | `dx`, `dy`, `capture="auto"` | Relative integer deltas −2000…2000. |
| `tv_click` | `capture="auto"` | Click at current pointer position. |
| `tv_scroll` | `dy`, `capture="auto"` | Integer scroll delta −100…100. |
| `tv_type` | `text`, `capture="auto"` | Focused-field LG IME input, 1–1000 characters; app support varies. |

## Capture and verification

- `auto`: capture unknown native routes/apps and unexpected foreground. For
  primitives it uses whether the app's launch is known, **not whether focus is known**.
  Request `always` whenever a screen decision needs fresh visual evidence.
- `always`: request an image. Capture can still fail or protected content can be blank.
- `never`: request lightweight state only; it does not establish content or focus.

Native `ok`/`app_verified` means the target app is foreground. `content_verified`
stays false. UI `tracked_state` is predicted. A command acknowledgement, launch
logo, active app or pointer PONG does not prove a search result or playback.

Unknown UI recipes, version changes and expired/mismatched starting context return
`executed:false` and request a screenshot. A failed in-flight batch may return
`executed:true` with an uncertain outcome; do not blindly replay it.

## Native example

```json
{"app_id":"youtube.leanback.v4","operation":"search","value":"10 minute stretching","capture":"auto"}
```

Inspect the resulting image, or request a fresh settled image with `tv_observe`.
Pass the latest returned `witness_id` to `tv_learn`; do not invent an ID. A witness
from another process/session is not accepted.

`open_url` is restricted to `com.webos.app.browser` and HTTP(S) URLs without
embedded credentials. `youtube_video` requires an 11-character YouTube video ID.
`search` requires an app-advertised supported template; no invented deep link is used.

## Internal UI example

This illustrates the format, not a preverified recipe for every TV/app. First
inspect your actual UI, then choose state names and steps matching it.

```json
{"witness_id":"<visual_witness_id from the current observation>","state":"home_page_search_button_focused_keyboard_closed"}
```

```json
{
  "name":"open_search",
  "from_state":"home_page_search_button_focused_keyboard_closed",
  "to_state":"search_page_input_focused_keyboard_open",
  "steps":[{"press":"ENTER"},{"wait":1}]
}
```

Only after inspecting the final screenshot and learning its witness can the route
be replayed. Never learn an unexpected result under the intended destination name.

Each step has one operation: `press`, `wait`, `text`, `replace_text` or `submit:true`.
Use `"text":"$VALUE"` or `"replace_text":"$VALUE"` for a parameter. A recipe has
1–20 steps, at most 5 seconds per wait, 10 seconds total wait and a 30-second
execution window, plus any native request already in flight. State names use
lowercase letters/numbers/underscores, at most 60 characters; route names at most 40.
Foreground is checked before each step. This cannot detect every popup or external key.

Do not record purchases, subscriptions, consent acceptance, account edits or
installations without specific authorization. This is agent guidance, not a semantic
classifier that can recognize every dangerous key sequence.

## CLI and persistent sessions

`lg-tv-use --help` lists supported standalone commands. Global options precede the
command. `--capture auto|never|always` controls images; captures are local files.
Standalone invocations reconnect, so learning witnesses require a persistent session:

```sh
lg-tv-use serve
```

Send one JSON object per line:

```json
{"command":"search","value":"10 minute stretching"}
{"command":"observe","capture":"always"}
{"command":"learn","witness_id":"<latest witness_id>","notes":"The query and matching results are visible"}
```

The session accepts `observe`, `apps`, `devices`, `select` (`target`), `register`
(`name`, `broadcast`), `power` (`operation`, `wait_seconds`), `learn`, `app_action`,
`launch`, `search`, `video`, `browser`, `press`, `move`, `click`, `scroll`, `text`,
`ui_anchor`, `ui_record`, `ui_run`. UI commands use the same fields as the MCP tools.
Other commands use the CLI field names shown by `--help`. End stdin to close sockets.
The pairing instruction line is human-readable; `serve` emits JSON result lines.

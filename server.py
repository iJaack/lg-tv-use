"""Resident stdio MCP adapter with learned native routes and visual fallback."""
import base64
import json
from threading import Lock
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent, ImageContent
from tv_use import discover
from tv_runtime import TVRuntime

Capture = Literal['auto', 'never', 'always']
mcp = FastMCP('LG TV Use', instructions='Use tv_devices and tv_select to address a paired TV explicitly. Use tv_apps for app versions and learned routes; launch_evidence may describe only a loading logo. Prefer tv_app_action native operations. Inspect unknown screens, then tv_learn after confirming the actual outcome. For UI discovery use tv_observe(always), tv_ui_anchor, tv_ui_record and tv_learn. An anchor must describe the underlying page, focus, keyboard and any modal, not just the selected tab. Inspect both origin and result; never learn an unexpected outcome. Use tv_ui_run for generated batches without images from that exact tracked state. Context expires after 60 seconds and is cleared by untracked commands or reconnects. Re-observe after external remote use or any uncertainty. Foreground and predicted tracked state do not prove content/focus/selection. Prefer native YouTube search to generic IME. tv_power verifies native power state; unreachable alone is not power-off proof. No purchases, subscriptions, consent, account changes or installation without specific authorization. Pair with CLI first.')
ui_lock = Lock()
runtime = TVRuntime()

def output(body, capture):
    blocks = [TextContent(type='text', text=json.dumps(body, ensure_ascii=False))]
    if capture:
        blocks.append(ImageContent(type='image', data=base64.b64encode(capture[0]).decode(), mimeType=capture[1]))
    return blocks

def action(method, *args, capture='auto'):
    with ui_lock:
        return output(*runtime.primitive(method, args, capture))

@mcp.tool()
def tv_discover() -> list[dict]:
    """Find LG TVs without pairing or subnet scanning."""
    return discover()

@mcp.tool()
def tv_observe(capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Lightweight app/audio state. Auto captures unknown apps; always requests a fresh screen."""
    with ui_lock:
        return output(*runtime.observe(capture))

@mcp.tool()
def tv_apps() -> dict:
    """Installed app versions, learned routes and native routes requiring visual discovery."""
    with ui_lock:
        return runtime.capabilities()

@mcp.tool()
def tv_devices() -> dict:
    """Known paired TVs. Select by name/ID; duplicate IPv4 addresses are ambiguous."""
    records=list(runtime.devices.read()['devices'].values())
    return {'devices':[{k:d[k] for k in ['id','name','host','model']} | {'address_conflict':sum(e['host']==d['host'] for e in records)>1} for d in records]}

@mcp.tool()
def tv_select(device: str) -> dict:
    """Select a registered TV by name or ID and verify native device identity. Never pair implicitly."""
    with ui_lock:
        return runtime.select(device)

@mcp.tool()
def tv_power(operation: Literal['on','off','status'], wait_seconds: int = 20) -> dict:
    """Power off natively or wake registered MACs, then verify native power state. Unreachable is not proof of power off. Wake requires TV network-standby support."""
    with ui_lock:
        return runtime.power(operation,wait_seconds)

@mcp.tool()
def tv_app_action(app_id: str, operation: Literal['launch', 'search', 'open_url', 'youtube_video'] = 'launch', value: str = '', capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Execute bounded native app route. Search uses advertised metadata; URL is LG browser only; video accepts YouTube ID. Unknown routes capture once for learning."""
    with ui_lock:
        return output(*runtime.app_action(app_id, operation, value, capture))

@mcp.tool()
def tv_ui_anchor(witness_id: str, state: str) -> dict:
    """Name exact page, focus, keyboard and modal state after inspecting a screenshot taken within 30 seconds. Selected tab alone is insufficient. Context expires after 60 seconds or an untracked command/reconnect."""
    with ui_lock:
        return runtime.ui_anchor(witness_id,state)

@mcp.tool()
def tv_ui_record(name: str, from_state: str, to_state: str, steps: list[dict], value: str = '') -> list[TextContent | ImageContent]:
    """From an inspected anchored state, run 1-20 press/wait/text/replace_text/submit steps and capture the result once. Inspect then tv_learn; text $VALUE parameterizes the route. Never learn loading as completed content, nor purchases, account edits or consent routes."""
    with ui_lock:
        return output(*runtime.ui_record(name,from_state,to_state,steps,value))

@mcp.tool()
def tv_ui_run(app_id: str, name: str, value: str = '') -> list[TextContent | ImageContent]:
    """Run generated UI addon without images only from its tracked origin and exact app version. Unknown/expired context returns a screenshot without executing. Tracked state is predicted, not TV focus readback; re-observe after external remote use."""
    with ui_lock:
        return output(*runtime.ui_run(app_id,name,value))

@mcp.tool()
def tv_learn(witness_id: str, notes: str) -> dict:
    """After visually verifying the latest native action or UI recording screenshot, compile its route into a reusable addon. Include what the image actually proved in notes."""
    with ui_lock:
        return runtime.learn(witness_id, notes)

@mcp.tool()
def tv_press(button: Literal['HOME', 'BACK', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'ENTER'], capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Bounded remote key; foreground state cannot identify focused control. Observe unknown navigation visually."""
    return action('press', button, capture=capture)

@mcp.tool()
def tv_move(dx: int, dy: int, capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Move relative pointer. Inspect unknown target before clicking."""
    return action('move', dx, dy, capture=capture)

@mcp.tool()
def tv_click(capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Click current pointer position; requires a known target or fresh visual observation."""
    return action('click', capture=capture)

@mcp.tool()
def tv_scroll(dy: int, capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Bounded scrolling, with lightweight state and automatic unknown-app fallback."""
    return action('scroll', dy, capture=capture)

@mcp.tool()
def tv_type(text: str, capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Send focused-field text. Some apps including YouTube ignore it; acknowledgement does not verify insertion."""
    return action('text', text, capture=capture)

@mcp.tool()
def tv_launch(app_id: str, capture: Capture = 'auto') -> list[TextContent | ImageContent]:
    """Launch installed non-stub app; learned route avoids image capture."""
    with ui_lock:
        return output(*runtime.app_action(app_id, 'launch', '', capture))

def main():
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()

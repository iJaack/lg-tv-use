"""CLI plus JSON-lines session for keeping one connection across commands."""
import argparse
import json
import os
from pathlib import Path
import time
import websocket
from tv_use import TVError, BUTTONS, discover, validate_host
from tv_runtime import TVRuntime


def dispatch(runtime, item):
    command = item.get('command')
    capture = item.get('capture', 'auto')
    if command == 'observe':
        return runtime.observe(capture)
    if command == 'apps':
        return runtime.capabilities(), None
    if command == 'devices':
        return {'devices':list(runtime.devices.read()['devices'].values())}, None
    if command == 'select':
        return runtime.select(item['target']), None
    if command == 'register':
        return runtime.register_device(item['name'],item['broadcast']), None
    if command == 'power':
        return runtime.power(item.get('operation','status'),item.get('wait_seconds',20)), None
    if command == 'learn':
        return runtime.learn(item['witness_id'], item.get('notes', '')), None
    if command == 'ui_anchor':
        return runtime.ui_anchor(item['witness_id'],item['state']),None
    if command == 'ui_record':
        return runtime.ui_record(item['name'],item['from_state'],item['to_state'],item['steps'],item.get('value',''))
    if command == 'ui_run':
        return runtime.ui_run(item['app_id'],item['name'],item.get('value',''))
    if command in {'launch', 'search', 'video', 'browser', 'app_action'}:
        aliases = {'launch': ('launch', item.get('app_id'), ''),
                   'search': ('search', item.get('app_id', 'youtube.leanback.v4'), item.get('value', '')),
                   'video': ('youtube_video', 'youtube.leanback.v4', item.get('value', '')),
                   'browser': ('open_url', 'com.webos.app.browser', item.get('value', '')),
                   'app_action': (item.get('operation', 'launch'), item.get('app_id'), item.get('value', ''))}
        operation, app_id, value = aliases[command]
        return runtime.app_action(app_id, operation, value, capture)
    arguments = {'press': [item.get('button')], 'move': [item.get('dx'), item.get('dy')],
                 'scroll': [item.get('dy')], 'text': [item.get('text')], 'click': []}
    if command not in arguments:
        raise TVError('Unsupported command')
    return runtime.primitive(command, arguments[command], capture)


def emit(body, capture):
    if capture:
        folder = Path('outputs'); folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / ('tv-' + str(time.time_ns()) + ('.jpg' if capture[1] == 'image/jpeg' else '.png'))
        path.write_bytes(capture[0]); os.chmod(path, 0o600)
        body['capture_path'] = str(path.resolve())
    print(json.dumps(body, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description='LG native routes, automatic addons, visual fallback')
    parser.add_argument('--host', default=os.environ.get('TV_HOST'), help='TV LAN IPv4 address (or TV_HOST)')
    parser.add_argument('--capture', choices=['auto', 'never', 'always'], default='auto')
    parser.add_argument('--model', default=os.environ.get('TV_MODEL'), help='Require native model identity before commands')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['discover', 'pair', 'observe', 'apps', 'devices', 'screenshot', 'click', 'serve']:
        sub.add_parser(name)
    sub.add_parser('press').add_argument('button', choices=sorted(BUTTONS))
    sub.add_parser('launch').add_argument('app_id')
    search = sub.add_parser('search'); search.add_argument('value'); search.add_argument('--app-id', default='youtube.leanback.v4')
    sub.add_parser('video').add_argument('value')
    sub.add_parser('browser').add_argument('value')
    sub.add_parser('text').add_argument('text')
    move = sub.add_parser('move'); move.add_argument('dx', type=int); move.add_argument('dy', type=int)
    sub.add_parser('scroll').add_argument('dy', type=int)
    learn = sub.add_parser('learn'); learn.add_argument('witness_id'); learn.add_argument('notes')
    sub.add_parser('select').add_argument('target')
    register=sub.add_parser('register');register.add_argument('name');register.add_argument('--broadcast',required=True)
    power=sub.add_parser('power');power.add_argument('operation',choices=['on','off','status']);power.add_argument('--wait-seconds',type=int,default=20)
    args = vars(parser.parse_args())
    if args['command'] == 'discover':
        emit({'devices': discover()}, None)
        return
    if not args['host']:
        parser.error('Set TV_HOST or pass --host with your TV LAN IPv4 address')
    try:
        validate_host(args['host'])
    except (TVError, ValueError) as error:
        parser.error(str(error))
    runtime = TVRuntime(host=args['host'],expected_model=args['model'])
    try:
        if args['command'] == 'pair':
            print('Accept LG TV Use on the TV within 45 seconds.', flush=True)
            emit(runtime.tv.connect(pair=True,expected_model=runtime.expected_model), None)
        elif args['command'] == 'serve':
            # No shell, eval, SSAP or arbitrary runtime method input is accepted.
            import sys
            for line in sys.stdin:
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise TVError('Expected JSON object')
                    emit(*dispatch(runtime, item))
                except (TVError, OSError, ValueError, KeyError, TypeError, websocket.WebSocketException) as error:
                    emit({'ok': False, 'error': str(error)}, None)
        else:
            if args['command'] == 'screenshot':
                args.update(command='observe', capture='always')
            body, image = dispatch(runtime, args)
            emit(body, image)
            if body.get('ok') is False:
                raise SystemExit(1)
    except (TVError, OSError, ValueError, websocket.WebSocketException) as error:
        emit({'ok': False, 'error': str(error)}, None)
        raise SystemExit(1)
    finally:
        runtime.close()

if __name__ == '__main__':
    main()

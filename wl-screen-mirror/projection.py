#!/usr/bin/env python3
"""One-shot output-layout transaction for the native projection panel.

Only runs while querying or changing displays. No shell commands or third-party
Python modules. A pending layout must be confirmed within 15 seconds.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def command(args):
    result = subprocess.run(['wlr-randr', *args], capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Output configuration was rejected')
    return result.stdout


def outputs():
    return json.loads(command(['--json']))


def emit(phase, **fields):
    print(json.dumps(dict(phase=phase, **fields)), flush=True)


def current_mode(output):
    return next((m for m in output['modes'] if m.get('current')), None)


def mode_args(output, custom=False):
    mode = current_mode(output)
    if mode is None:
        return ['--preferred']
    value = f"{mode['width']}x{mode['height']}@{mode['refresh']:.6f}Hz"
    return ['--custom-mode' if custom else '--mode', value]


def layout(snapshot, mode, source, destination):
    if mode not in ('laptop', 'duplicate', 'extend', 'external'):
        raise ValueError('Unknown projection mode')
    by_name = {o['name']: o for o in snapshot}
    if source not in by_name:
        raise ValueError('The source display is disconnected')
    if mode != 'laptop' and (destination not in by_name or source == destination):
        raise ValueError('Choose a different connected external display')
    # Enable the source before referring to it with --right-of, independent of
    # compositor enumeration order. Other connected displays are disabled.
    order = [by_name[source]] + [o for o in snapshot if o['name'] != source]
    args = []
    for output in order:
        name = output['name']
        args += ['--output', name]
        if name == source and mode != 'external':
            args += ['--on', *mode_args(output), '--pos', '0,0']
        elif name == destination and mode != 'laptop':
            args += ['--on', *mode_args(output)]
            args += ['--pos', '0,0'] if mode == 'external' else ['--right-of', source]
        else:
            args += ['--off']
    return args


def restore(snapshot):
    connected = {o['name'] for o in outputs()}
    surviving = [o for o in snapshot if o['name'] in connected]
    if not any(o['enabled'] for o in surviving):
        # External-only mode may lose its display. Recover a surviving panel
        # instead of applying an all-disabled snapshot.
        fallback = next((o for o in surviving if o['name'].startswith(('eDP-', 'LVDS-', 'DSI-'))), None)
        fallback = fallback or next(iter(surviving), None)
        if fallback is None:
            raise RuntimeError('No connected display is available for recovery')
        command(['--output', fallback['name'], '--on', '--preferred'])
        return
    for custom in (False, True):
        args = []
        for output in surviving:
            args += ['--output', output['name']]
            if not output['enabled']:
                args += ['--off']
                continue
            pos = output['position']
            args += ['--on', *mode_args(output, custom), '--pos', f"{pos['x']},{pos['y']}",
                     '--scale', str(output['scale']), '--transform', output['transform'],
                     '--adaptive-sync', 'enabled' if output['adaptive_sync'] else 'disabled']
        try:
            command(['--dryrun', *args])
        except RuntimeError:
            if custom:
                raise
            continue
        command(args)
        return


def transaction(mode, source, destination, control, seconds=15):
    snapshot = outputs()
    args = layout(snapshot, mode, source, destination)
    if mode == 'laptop' and {o['name'] for o in snapshot if o.get('enabled')} == {source}:
        emit('unchanged', mode=mode, source=source, destination=destination)
        return
    command(['--dryrun', *args])
    keep = False
    applied = False
    interrupted = False

    def stop(_number, _frame):
        nonlocal interrupted
        interrupted = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        if interrupted:
            return
        # An application can fail after changing an output: attempt recovery
        # even when the command does not report success.
        applied = True
        command(args)
        deadline = time.monotonic() + seconds
        last_remaining = None
        while not interrupted and time.monotonic() < deadline:
            try:
                decision = control.read_text().strip()
            except FileNotFoundError:
                decision = 'revert'
            if decision == 'keep':
                keep = True
                break
            if decision == 'revert':
                break
            remaining = max(1, int(deadline - time.monotonic() + .999))
            if remaining != last_remaining:
                emit('pending', seconds=remaining, mode=mode, source=source, destination=destination)
                last_remaining = remaining
            time.sleep(.1)
    finally:
        try:
            if applied and not keep:
                restore(snapshot)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    emit('kept' if keep else 'reverted', mode=mode, source=source, destination=destination)


def main():
    if sys.argv[1:] == ['list']:
        print(json.dumps(outputs()))
        return
    if len(sys.argv) != 6 or sys.argv[1] != 'apply':
        raise ValueError('Usage: projection.py list | apply MODE SOURCE DESTINATION CONTROL_FILE')
    control = Path(sys.argv[5])
    # Serialize transactions, including across a Noctalia plugin reload.
    with control.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transaction(sys.argv[2], sys.argv[3], sys.argv[4], control)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)

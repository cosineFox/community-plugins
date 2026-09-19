#!/usr/bin/env python3
"""One-shot output-layout transaction for the native projection panel."""
import copy
import fcntl
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import time


INTERNAL_PREFIXES = ('eDP-', 'LVDS-', 'DSI-')
COORDINATE_LIMIT = 1000000
MAX_OUTPUTS = 32
TRANSFORMS = {'normal', '90', '180', '270', 'flipped', 'flipped-90', 'flipped-180', 'flipped-270'}
MODE_REFRESH_TOLERANCE = .001


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('Invalid output baseline')
    return value


def _integer(value):
    # Luau's JSON bridge serializes all numbers as doubles, including whole
    # coordinates and mode sizes. Accept integral floats, never fractions/bools.
    _number(value)
    if value != math.trunc(value) or abs(value) > COORDINATE_LIMIT:
        raise ValueError('Invalid integer coordinate or dimension')
    return int(value)


def _position(output):
    value = output.get('position')
    if not isinstance(value, dict) or set(value) != {'x', 'y'}:
        raise ValueError('Invalid output baseline')
    return {key: _integer(coordinate) for key, coordinate in value.items()}


def _mode(output, preferred=False):
    modes = output.get('modes')
    if not isinstance(modes, list):
        raise ValueError('Invalid output baseline')
    mode = next((item for item in modes if isinstance(item, dict) and item.get('preferred' if preferred else 'current')), None)
    if mode is None:
        raise ValueError('Invalid output baseline')
    if _integer(mode.get('width')) <= 0 or _integer(mode.get('height')) <= 0:
        raise ValueError('Invalid output baseline')
    _number(mode.get('refresh'), positive=True)
    return mode


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, list) or not snapshot or len(snapshot) > MAX_OUTPUTS:
        raise ValueError('Invalid output baseline')
    names = set()
    for output in snapshot:
        if not isinstance(output, dict) or not isinstance(output.get('name'), str) or not output['name'] or output['name'] in names or not isinstance(output.get('enabled'), bool):
            raise ValueError('Invalid output baseline')
        names.add(output['name'])
        if output['enabled']:
            _position(output)
            _number(output.get('scale', 1), positive=True)
            if (not isinstance(output.get('transform', 'normal'), str) or output.get('transform', 'normal') not in TRANSFORMS or
                    not isinstance(output.get('adaptive_sync', False), bool)):
                raise ValueError('Invalid output baseline')
            _mode(output)
    return snapshot


def command(args):
    result = subprocess.run(['wlr-randr', *args], capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Output configuration was rejected')
    return result.stdout


def outputs():
    """Return wlr-randr's raw output array; callers use this as a baseline."""
    return json.loads(command(['--json']))


def describe(snapshot):
    """Add UI-only logical dimensions without changing the raw output snapshot."""
    _validate_snapshot(snapshot)
    result = []
    for output in snapshot:
        item = dict(output)
        width, height = logical_size(output)
        item['logical_width'], item['logical_height'] = width, height
        result.append(item)
    return result


def emit(phase, **fields):
    print(json.dumps(dict(phase=phase, **fields)), flush=True)


def current_mode(output):
    modes = output.get('modes', [])
    return next((mode for mode in modes if isinstance(mode, dict) and mode.get('current')), None)


def target_mode(output):
    return current_mode(output) or next((mode for mode in output.get('modes', []) if isinstance(mode, dict) and mode.get('preferred')), None)


def mode_args(output, custom=False):
    mode = _mode(output, preferred=not bool(current_mode(output)))
    value = f"{mode['width']}x{mode['height']}@{mode['refresh']:.6f}Hz"
    return ['--custom-mode' if custom else '--mode', value]


def logical_size(output):
    if not output.get('enabled') and target_mode(output) is None:
        return (0, 0)
    mode = _mode(output, preferred=not bool(current_mode(output)))
    scale = _number(output.get('scale', 1), positive=True)
    # Match wlr-randr 0.5.0 head_width/head_height and wlroots 0.20's
    # wlr_output_effective_resolution: positive sizes truncate after division.
    width = math.floor(mode['width'] / scale)
    height = math.floor(mode['height'] / scale)
    if output.get('transform', 'normal') in ('90', '270', 'flipped-90', 'flipped-270'):
        width, height = height, width
    return width, height


def position(output):
    return _position(output)


def is_internal(output):
    return output['name'].startswith(INTERNAL_PREFIXES)


def output_args(output):
    args = ['--output', output['name']]
    if not output.get('enabled'):
        return args + ['--off']
    pos = position(output)
    return args + ['--on', *mode_args(output), '--pos', f"{pos['x']},{pos['y']}",
                   '--scale', str(output.get('scale', 1)), '--transform', output.get('transform', 'normal'),
                   '--adaptive-sync', 'enabled' if output.get('adaptive_sync') else 'disabled']


def changed(before, after):
    if before.get('enabled') != after.get('enabled'):
        return True
    return bool(after.get('enabled')) and position(before) != position(after)


def desktop_edge(active):
    if not active:
        return 0, 0
    return (max(position(output)['x'] + logical_size(output)[0] for output in active),
            min(position(output)['y'] for output in active))


def place_new(desired, original, names):
    active = [output for output in desired if output.get('enabled') and output['name'] not in names]
    x, y = desktop_edge(active)
    for output in desired:
        if output['name'] in names and not original[output['name']].get('enabled'):
            output['position'] = {'x': x, 'y': y}
            x += logical_size(output)[0]


def desired_preset(snapshot, mode, source, destination):
    _validate_snapshot(snapshot)
    if mode not in ('laptop', 'single', 'duplicate', 'extend', 'external'):
        raise ValueError('Unknown projection mode')
    by_name = {output['name']: output for output in snapshot}
    if mode in ('laptop', 'single', 'duplicate') and source not in by_name:
        raise ValueError('The source display is disconnected')
    if mode == 'laptop' and not is_internal(by_name[source]):
        raise ValueError('Laptop only requires a built-in display')
    if mode == 'duplicate' and (destination not in by_name or source == destination):
        raise ValueError('Choose a different connected external display')
    desired = copy.deepcopy(snapshot)
    desired_by_name = {output['name']: output for output in desired}
    if mode in ('laptop', 'single'):
        for output in desired:
            output['enabled'] = output['name'] == source
        if not by_name[source]['enabled']:
            desired_by_name[source]['position'] = {'x': 0, 'y': 0}
    elif mode == 'extend':
        new = {output['name'] for output in snapshot if not output.get('enabled')}
        for output in desired:
            output['enabled'] = True
        place_new(desired, by_name, new)
    elif mode == 'external':
        new = {output['name'] for output in snapshot if not output.get('enabled') and not is_internal(output)}
        for output in desired:
            output['enabled'] = not is_internal(output)
        place_new(desired, by_name, new)
    else:  # duplicate: alter only the requested pair, leaving other heads alone.
        new = {name for name in (source, destination) if not by_name[name].get('enabled')}
        for name in (source, destination):
            desired_by_name[name]['enabled'] = True
        place_new(desired, by_name, new)
    validate_desired(desired)
    return mode, desired


def validate_desired(desired):
    """Validate targets before dry-run, including newly enabled heads without a current mode."""
    active = [output for output in desired if output.get('enabled')]
    if not active:
        raise ValueError('At least one output must be enabled')
    for output in active:
        position(output)
        _number(output.get('scale', 1), positive=True)
        if output.get('transform', 'normal') not in TRANSFORMS:
            raise ValueError('Invalid output transform')
        if not isinstance(output.get('adaptive_sync', False), bool):
            raise ValueError('Invalid adaptive sync state')
        if min(logical_size(output)) <= 0:
            raise ValueError('An enabled output requires a usable mode')


def layout(snapshot, mode, source, destination):
    """Build a preset command, changing only outputs whose enabled/position state changes."""
    _mode, desired = desired_preset(snapshot, mode, source, destination)
    original = {output['name']: output for output in snapshot}
    # Enable changed outputs first so a newly enabled source is usable by a compositor.
    changes = [output for output in desired if changed(original[output['name']], output)]
    changes.sort(key=lambda output: not output.get('enabled'))
    return [arg for output in changes for arg in output_args(output)]


def fingerprint(snapshot):
    """Canonical observed topology, excluding annotations and meaningless disabled metadata."""
    _validate_snapshot(snapshot)
    result = []
    for output in snapshot:
        if not output['enabled']:
            result.append((output['name'], False))
            continue
        mode = _mode(output)
        pos = position(output)
        result.append((output['name'], True, mode['width'], mode['height'], mode['refresh'],
                       pos['x'], pos['y'], output.get('scale', 1), output.get('transform', 'normal'),
                       output.get('adaptive_sync', False)))
    return tuple(sorted(result))


def validate_layout(request, snapshot):
    _validate_snapshot(snapshot)
    if not isinstance(request, dict) or not isinstance(request.get('outputs'), list):
        raise ValueError('Layout request must contain an outputs array')
    by_name = {output['name']: output for output in snapshot}
    requested = request['outputs']
    if len(requested) != len(by_name):
        raise ValueError('Layout request must include every connected output exactly once')
    desired = copy.deepcopy(snapshot)
    desired_by_name = {output['name']: output for output in desired}
    seen = set()
    for item in requested:
        if not isinstance(item, dict) or set(item) != {'name', 'enabled', 'x', 'y'}:
            raise ValueError('Each layout output requires name, enabled, x, and y')
        name = item['name']
        if not isinstance(name, str) or name not in by_name or name in seen:
            raise ValueError('Layout request has an unknown or duplicate output')
        if not isinstance(item['enabled'], bool):
            raise ValueError('Layout enabled state is invalid')
        coordinates = {key: _integer(item[key]) for key in ('x', 'y')}
        seen.add(name)
        desired_by_name[name]['enabled'] = item['enabled']
        desired_by_name[name]['position'] = coordinates
    validate_desired(desired)
    active = [output for output in desired if output['enabled']]
    for index, left in enumerate(active):
        lx, ly = position(left)['x'], position(left)['y']
        lw, lh = logical_size(left)
        for right in active[index + 1:]:
            rx, ry = position(right)['x'], position(right)['y']
            rw, rh = logical_size(right)
            if lx < rx + rw and rx < lx + lw and ly < ry + rh and ry < ly + lh:
                raise ValueError('Enabled output rectangles overlap')
    return desired


def restore(snapshot):
    _validate_snapshot(snapshot)
    current_snapshot = outputs()
    _validate_snapshot(current_snapshot)
    current = {output['name']: output for output in current_snapshot}
    original = {output['name']: output for output in snapshot}
    # Preserve heads that appeared after the preview: they were not ours to disable.
    desired = [copy.deepcopy(original.get(name, output)) for name, output in current.items()]
    if not any(output.get('enabled') for output in desired):
        fallback = next((output for output in desired if is_internal(output)), None) or next(iter(desired), None)
        if fallback is None:
            raise RuntimeError('No connected display is available for recovery')
        fallback['enabled'] = True
        fallback['position'] = {'x': 0, 'y': 0}
    args = [arg for output in desired for arg in output_args(output)]
    try:
        command(['--dryrun', *args])
    except RuntimeError:
        # Recreate current modes only when the normal mode spelling was rejected.
        args = [arg for output in desired for arg in output_args_custom(output)]
        command(['--dryrun', *args])
    command(args)


def output_args_custom(output):
    args = output_args(output)
    return ['--custom-mode' if arg == '--mode' else arg for arg in args]


def verify(expected, changed_names=None):
    """Verify every expected head; disabled heads have no meaningful geometry."""
    actual = outputs()
    try:
        _validate_snapshot(actual)
        expected_by_name = {output['name']: output for output in expected}
        actual_by_name = {output['name']: output for output in actual}
        if set(expected_by_name) != set(actual_by_name):
            raise ValueError
        for name, wanted in expected_by_name.items():
            got = actual_by_name[name]
            if got['enabled'] != wanted.get('enabled'):
                raise ValueError
            if not wanted.get('enabled'):
                continue
            wanted_mode = target_mode(wanted)
            got_mode = _mode(got)
            if (wanted_mode is None or got_mode['width'] != wanted_mode.get('width') or
                    got_mode['height'] != wanted_mode.get('height') or
                    abs(got_mode['refresh'] - wanted_mode.get('refresh', float('nan'))) > MODE_REFRESH_TOLERANCE or
                    position(got) != position(wanted) or
                    abs(got.get('scale', 1) - wanted.get('scale', 1)) > 0.000001 or
                    got.get('transform', 'normal') != wanted.get('transform', 'normal') or
                    got.get('adaptive_sync', False) != wanted.get('adaptive_sync', False)):
                raise ValueError
        return actual
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Output layout could not be verified') from None


def run_transaction(snapshot, desired, mode, source, destination, control, seconds=15):
    before = {output['name']: output for output in snapshot}
    changed_names = {output['name'] for output in desired if changed(before[output['name']], output)}
    metadata = dict(mode=mode, source=source, destination=destination)
    if not changed_names:
        emit('unchanged', **metadata)
        return
    args = [arg for output in sorted((output for output in desired if output['name'] in changed_names),
                                    key=lambda output: not output.get('enabled')) for arg in output_args(output)]
    command(['--dryrun', *args])
    # dry-run and apply are separate protocol transactions. Recheck as late as
    # possible, before sending a command based on a now-stale snapshot.
    if fingerprint(outputs()) != fingerprint(snapshot):
        raise ValueError('Output topology changed; refresh displays and try again')
    keep = applied = interrupted = False

    def stop(_number, _frame):
        nonlocal interrupted
        interrupted = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        if interrupted:
            return
        applied = True
        command(args)
        actual = verify(desired, changed_names)
        # The compositor may set current-mode flags while enabling a head.  Its
        # verified readback, rather than the pre-apply draft, is the pending baseline.
        expected_fingerprint = fingerprint(actual)
        deadline, last_remaining, next_topology_query = time.monotonic() + seconds, None, 0
        while not interrupted and time.monotonic() < deadline:
            now = time.monotonic()
            # Poll control responsively, but ask wlr-randr for topology at most once
            # per second while pending.
            if now >= next_topology_query:
                if fingerprint(outputs()) != expected_fingerprint:
                    break
                next_topology_query = now + 1
            decision = control.read_text().strip() if control.exists() else 'revert'
            if decision == 'keep':
                # Re-query immediately before retaining the layout to close the
                # interval between the throttled poll and this decision.
                if fingerprint(outputs()) == expected_fingerprint:
                    keep = True
                break
            if decision == 'revert':
                break
            remaining = max(1, int(deadline - now + .999))
            if remaining != last_remaining:
                emit('pending', seconds=remaining, **metadata)
                last_remaining = remaining
            time.sleep(.1)
    finally:
        try:
            if applied and not keep:
                restore(snapshot)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    emit('kept' if keep else 'reverted', **metadata)


def transaction(mode, source, destination, control, seconds=15):
    snapshot = outputs()
    actual_mode, desired = desired_preset(snapshot, mode, source, destination)
    run_transaction(snapshot, desired, actual_mode, source, destination, control, seconds)


def apply_layout(request, control, seconds=15):
    if not isinstance(request, dict) or 'baseline' not in request:
        raise ValueError('Layout request must contain a baseline')
    baseline = request['baseline']
    if not isinstance(baseline, list):
        raise ValueError('Invalid output baseline')
    snapshot = outputs()
    if fingerprint(baseline) != fingerprint(snapshot):
        raise ValueError('Output topology changed; refresh displays and try again')
    desired = validate_layout(request, snapshot)
    run_transaction(snapshot, desired, 'layout', '', '', control, seconds)


def main():
    if sys.argv[1:] == ['list']:
        print(json.dumps(describe(outputs())))
        return
    if len(sys.argv) == 3 and sys.argv[1] == 'settle':
        # Used only after service reload: wait for the old helper's rollback
        # before the replacement service attempts to resume a managed mirror.
        with (Path(sys.argv[2]) / 'projection.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            print(json.dumps(describe(outputs())))
        return
    if len(sys.argv) == 6 and sys.argv[1] == 'apply':
        control = Path(sys.argv[5])
        action = lambda: transaction(sys.argv[2], sys.argv[3], sys.argv[4], control)
    elif len(sys.argv) == 4 and sys.argv[1] == 'apply-layout':
        request_file, control = Path(sys.argv[2]), Path(sys.argv[3])
        action = lambda: apply_layout(json.loads(request_file.read_text()), control)
    else:
        raise ValueError('Usage: projection.py list | apply MODE SOURCE DESTINATION CONTROL_FILE | apply-layout REQUEST_FILE CONTROL_FILE')
    # All uniquely named controls share one lock, including across service reloads.
    with (control.parent / 'projection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        action()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)

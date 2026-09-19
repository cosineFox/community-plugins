"""Mocked regression tests for projection.py; never query a live Wayland session."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('projection', Path(__file__).resolve().parents[1] / 'projection.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def display(name, enabled=True, width=1920, height=1200, scale=1.25, transform='normal', x=0, y=0):
    return dict(name=name, enabled=enabled,
                modes=[dict(width=width, height=height, refresh=60.0, current=enabled, preferred=True)],
                position=dict(x=x, y=y), scale=scale, transform=transform, adaptive_sync=False)


def observed(snapshot):
    """Mock compositor readback: enabled preferred modes become current."""
    result = copy.deepcopy(snapshot)
    for output in result:
        for mode in output.get('modes', []):
            mode['current'] = bool(output['enabled'] and mode.get('preferred'))
    return result


class Projection(unittest.TestCase):
    def setUp(self):
        self.screens = [display('eDP-1', True), display('HDMI-A-1', True, x=1536),
                        display('DP-1', False, width=2560, height=1440, scale=1)]

    def test_presets_keep_unrelated_heads_and_enable_all_when_required(self):
        mode, desired = p.desired_preset(self.screens, 'extend', 'eDP-1', 'HDMI-A-1')
        self.assertEqual(mode, 'extend')
        self.assertTrue(all(output['enabled'] for output in desired))
        self.assertEqual(desired[2]['position'], {'x': 3072, 'y': 0})

        _, desired = p.desired_preset(self.screens, 'external', 'eDP-1', 'HDMI-A-1')
        self.assertFalse(desired[0]['enabled'])
        self.assertTrue(desired[1]['enabled'])
        self.assertTrue(desired[2]['enabled'])

        snapshot = copy.deepcopy(self.screens)
        snapshot[2]['enabled'] = True
        snapshot[2]['modes'][0]['current'] = True
        snapshot[2]['position'] = {'x': -2560, 'y': 0}
        _, desired = p.desired_preset(snapshot, 'duplicate', 'eDP-1', 'HDMI-A-1')
        self.assertEqual(desired[2]['position'], {'x': -2560, 'y': 0})
        self.assertTrue(desired[2]['enabled'])
        self.assertEqual(p.desired_preset(self.screens, 'single', 'eDP-1', 'HDMI-A-1')[0], 'single')

    def test_duplicate_new_heads_are_placed_without_moving_enabled_heads(self):
        snapshot = [display('eDP-1', False), display('HDMI-A-1', False),
                    display('DP-1', True, x=-1000, width=1000, height=800, scale=1)]
        _, desired = p.desired_preset(snapshot, 'duplicate', 'eDP-1', 'HDMI-A-1')
        self.assertEqual(desired[2]['position'], {'x': -1000, 'y': 0})
        self.assertEqual(desired[0]['position'], {'x': 0, 'y': 0})
        self.assertEqual(desired[1]['position'], {'x': 1536, 'y': 0})

    def test_invalid_preset_fails_before_any_command(self):
        with patch.object(p, 'command') as command:
            for mode, source, dest in [('oops', 'eDP-1', 'HDMI-A-1'),
                                       ('laptop', 'missing', ''),
                                       ('duplicate', 'eDP-1', 'unplugged')]:
                with self.assertRaises(ValueError):
                    p.layout(self.screens, mode, source, dest)
            command.assert_not_called()

    def test_layout_validation_handles_negative_mixed_scale_and_portrait(self):
        screens = [display('eDP-1', True, 1920, 1080, 1.25, x=-1536),
                   display('DP-1', True, 1080, 1920, 1.5, '90', x=0)]
        request = {'outputs': [{'name': 'eDP-1', 'enabled': True, 'x': -1536, 'y': 0},
                               {'name': 'DP-1', 'enabled': True, 'x': 0, 'y': 0}]}
        desired = p.validate_layout(request, screens)
        self.assertEqual(p.logical_size(desired[1]), (1280, 720))
        self.assertEqual(desired[0]['position']['x'], -1536)
        for bad in (
            {'outputs': request['outputs'][:1]},
            {'outputs': request['outputs'] + [request['outputs'][0]]},
            {'outputs': [{'name': 'eDP-1', 'enabled': False, 'x': 0, 'y': 0},
                         {'name': 'DP-1', 'enabled': False, 'x': 0, 'y': 0}]},
            {'outputs': [{'name': 'eDP-1', 'enabled': True, 'x': 0, 'y': 0},
                         {'name': 'DP-1', 'enabled': True, 'x': 0, 'y': 0}]},
        ):
            with self.assertRaises(ValueError):
                p.validate_layout(bad, screens)

    def test_fingerprint_ignores_annotations_order_and_noncurrent_modes(self):
        baseline = copy.deepcopy(self.screens)
        baseline.reverse()
        baseline[0]['logical_width'] = 1
        baseline[0]['modes'].append(dict(width=1, height=1, refresh=1, preferred=False))
        self.assertEqual(p.fingerprint(baseline), p.fingerprint(self.screens))
        baseline[next(i for i, output in enumerate(baseline) if output['name'] == 'eDP-1')]['position']['x'] = 9
        self.assertNotEqual(p.fingerprint(baseline), p.fingerprint(self.screens))

    def test_apply_layout_rejects_stale_baseline_before_mutation(self):
        request = {'baseline': copy.deepcopy(self.screens),
                   'outputs': [{'name': o['name'], 'enabled': o['enabled'], 'x': o['position']['x'], 'y': 0}
                               for o in self.screens]}
        request['baseline'][0]['position']['x'] = 1
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'topology changed'):
                p.apply_layout(request, Path('/unused'))
            command.assert_not_called()

    def test_noop_emits_unchanged_for_each_preset_and_layout(self):
        cases = [
            ('laptop', [display('eDP-1'), display('HDMI-A-1', False)], 'eDP-1', 'HDMI-A-1'),
            ('extend', [display('eDP-1'), display('HDMI-A-1', True, x=1536)], 'eDP-1', 'HDMI-A-1'),
            ('external', [display('eDP-1', False), display('HDMI-A-1', True)], 'eDP-1', 'HDMI-A-1'),
            ('duplicate', [display('eDP-1'), display('HDMI-A-1', True, x=1536), display('DP-1', True, x=3072)], 'eDP-1', 'HDMI-A-1'),
        ]
        for mode, screens, source, destination in cases:
            with self.subTest(mode=mode), patch.object(p, 'outputs', return_value=screens), patch.object(p, 'command') as command, patch.object(p, 'emit') as emit:
                p.transaction(mode, source, destination, Path('/unused'))
                command.assert_not_called()
                emit.assert_called_once_with('unchanged', mode=mode, source=source, destination=destination)
        request = {'baseline': self.screens, 'outputs': [dict(name=o['name'], enabled=o['enabled'], x=o['position']['x'], y=o['position']['y']) for o in self.screens]}
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command') as command, patch.object(p, 'emit') as emit:
            p.apply_layout(request, Path('/unused'))
            command.assert_not_called()
            emit.assert_called_once_with('unchanged', mode='layout', source='', destination='')

    def test_transaction_keep_timeout_failed_apply_and_readback_failure(self):
        _, desired = p.desired_preset(self.screens, 'extend', 'eDP-1', 'HDMI-A-1')
        for decision in ('keep', 'timeout', 'failed-apply', 'readback-failure'):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as temp:
                control = Path(temp) / 'control'
                control.write_text(decision)
                calls = []
                def command(args):
                    calls.append(args)
                    if decision == 'failed-apply' and len(calls) == 2:
                        raise RuntimeError('failed')
                    return ''
                readback = self.screens if decision == 'readback-failure' else observed(desired)
                with patch.object(p, 'outputs', side_effect=[copy.deepcopy(self.screens), copy.deepcopy(self.screens), copy.deepcopy(readback), observed(desired), observed(desired)]), \
                     patch.object(p, 'command', side_effect=command), patch.object(p, 'restore') as restore, patch.object(p, 'emit'):
                    if decision in ('failed-apply', 'readback-failure'):
                        with self.assertRaises(RuntimeError):
                            p.transaction('extend', 'eDP-1', 'HDMI-A-1', control)
                        restore.assert_called_once()
                    else:
                        p.transaction('extend', 'eDP-1', 'HDMI-A-1', control, seconds=0 if decision == 'timeout' else 1)
                        self.assertEqual(restore.called, decision == 'timeout')

    def test_keep_refuses_unplug_and_restore_preserves_new_output(self):
        _, desired = p.desired_preset(self.screens, 'extend', 'eDP-1', 'HDMI-A-1')
        unplugged = [copy.deepcopy(desired[0]), copy.deepcopy(desired[1])]
        unplugged = observed(unplugged)
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / 'control'
            control.write_text('keep')
            with patch.object(p, 'outputs', side_effect=[self.screens, self.screens, observed(desired), unplugged]), patch.object(p, 'command'), patch.object(p, 'restore') as restore, patch.object(p, 'emit') as emit:
                p.transaction('extend', 'eDP-1', 'HDMI-A-1', control)
                restore.assert_called_once()
                self.assertEqual(emit.call_args.args[0], 'reverted')
        new = display('DP-2', True, x=5000)
        with patch.object(p, 'outputs', return_value=[self.screens[0], new]), patch.object(p, 'command') as command:
            p.restore(self.screens)
            args = command.call_args.args[0]
            self.assertIn('DP-2', args)
            self.assertNotIn(['--output', 'DP-2', '--off'], args)

    def test_restore_preserves_mode_scale_rotation_and_custom_mode_fallback(self):
        snapshot = [display('eDP-1', True, scale=1.5, transform='90', x=-1200, y=40),
                    display('HDMI-A-1', False)]
        with patch.object(p, 'outputs', return_value=snapshot), patch.object(p, 'command') as command:
            p.restore(snapshot)
            args = command.call_args.args[0]
            for value in ('1920x1200@60.000000Hz', '1.5', '90', '-1200,40'):
                self.assertIn(value, args)
        with patch.object(p, 'outputs', return_value=snapshot), \
             patch.object(p, 'command', side_effect=[RuntimeError('mode missing'), '', '']) as command:
            p.restore(snapshot)
            self.assertIn('--custom-mode', command.call_args.args[0])

    def test_dryrun_happens_before_apply_and_rejection_does_not_restore(self):
        with patch.object(p, 'outputs', return_value=self.screens), \
             patch.object(p, 'command', side_effect=RuntimeError('rejected')) as command, \
             patch.object(p, 'restore') as restore:
            with self.assertRaisesRegex(RuntimeError, 'rejected'):
                p.transaction('extend', 'eDP-1', 'HDMI-A-1', Path('/unused'))
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0][0], '--dryrun')
            restore.assert_not_called()

    def test_disabled_enable_uses_preferred_readback_and_keep_uses_canonical_actual(self):
        _, desired = p.desired_preset(self.screens, 'extend', '', '')
        self.assertFalse(desired[2]['modes'][0]['current'])
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / 'control'
            control.write_text('keep')
            actual = observed(desired)
            with patch.object(p, 'outputs', side_effect=[self.screens, self.screens, actual, actual, actual]), \
                 patch.object(p, 'command') as command, patch.object(p, 'restore') as restore, patch.object(p, 'emit') as emit:
                p.transaction('extend', '', '', control)
                self.assertIn('--on', command.call_args.args[0])
                restore.assert_not_called()
                self.assertEqual(emit.call_args.args[0], 'kept')
                self.assertEqual(emit.call_args.kwargs, {'mode': 'extend', 'source': '', 'destination': ''})

    def test_keep_rechecks_stale_topology_and_untouched_property_drift(self):
        _, desired = p.desired_preset(self.screens, 'extend', '', '')
        actual = observed(desired)
        stale = observed(desired)
        stale[1]['scale'] = 2
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / 'control'
            control.write_text('keep')
            with patch.object(p, 'outputs', side_effect=[self.screens, self.screens, actual, actual, stale]), \
                 patch.object(p, 'command'), patch.object(p, 'restore') as restore, patch.object(p, 'emit') as emit:
                p.transaction('extend', '', '', control)
                restore.assert_called_once()
                self.assertEqual(emit.call_args.args[0], 'reverted')
                self.assertEqual(emit.call_args.kwargs, {'mode': 'extend', 'source': '', 'destination': ''})
        with patch.object(p, 'outputs', return_value=stale):
            with self.assertRaisesRegex(RuntimeError, 'verified'):
                p.verify(desired)

    def test_disabled_positions_are_ignored_and_layout_validation_is_strict(self):
        snapshot = copy.deepcopy(self.screens)
        snapshot[2].pop('position')
        request = {'outputs': [
            {'name': 'eDP-1', 'enabled': True, 'x': 0, 'y': 0},
            {'name': 'HDMI-A-1', 'enabled': True, 'x': 1536, 'y': 0},
            {'name': 'DP-1', 'enabled': False, 'x': 999, 'y': -999},
        ]}
        desired = p.validate_layout(request, snapshot)
        self.assertFalse(p.changed(snapshot[2], desired[2]))
        self.assertEqual(p.fingerprint(snapshot), p.fingerprint(desired))
        actual = copy.deepcopy(desired)
        actual[2].pop('position')
        with patch.object(p, 'outputs', return_value=actual):
            p.verify(desired)
        self.assertEqual(p.output_args({'name': 'DP-1', 'enabled': False}), ['--output', 'DP-1', '--off'])
        malformed = copy.deepcopy(self.screens)
        malformed[0]['scale'] = float('nan')
        for value in (malformed, {'not': 'an array'}, [dict(name='', enabled=True)]):
            with self.assertRaises(ValueError):
                p.fingerprint(value)
        bad_request = {'outputs': [dict(name=output['name'], enabled=output['enabled'], x=output['position']['x'], y=output['position']['y']) for output in self.screens]}
        bad_request['outputs'][0]['x'] = True
        with self.assertRaises(ValueError):
            p.validate_layout(bad_request, self.screens)

    def test_transforms_and_noop_presets_preserve_existing_coordinates(self):
        self.assertEqual(p.logical_size(display('DP-1', True, 1080, 1920, 1.5, 'normal')), (720, 1280))
        self.assertEqual(p.logical_size(display('DP-1', True, 1080, 1920, 1.5, 'flipped')), (720, 1280))
        for transform in ('90', '270', 'flipped-90', 'flipped-270'):
            with self.subTest(transform=transform):
                self.assertEqual(p.logical_size(display('DP-1', True, 1080, 1920, 1.5, transform)), (1280, 720))
        shifted = [display('eDP-1', True, x=91), display('HDMI-A-1', False, x=400)]
        _, desired = p.desired_preset(shifted, 'laptop', 'eDP-1', '')
        self.assertEqual(desired[0]['position']['x'], 91)
        self.assertEqual(p.layout(shifted, 'laptop', 'eDP-1', ''), [])
        self.assertEqual(p.desired_preset(shifted, 'extend', '', '')[0], 'extend')
        self.assertEqual(p.desired_preset(shifted, 'external', '', '')[0], 'external')

    def test_luau_json_integral_doubles_roundtrip_without_rejecting_baseline(self):
        request = {'baseline': self.screens, 'outputs': [dict(name=o['name'], enabled=o['enabled'], x=o['position']['x'], y=0) for o in self.screens]}
        # luaToJson() represents every Lua number as a JSON double.
        request = json.loads(json.dumps(request), parse_int=float)
        self.assertIsInstance(request['baseline'][0]['modes'][0]['width'], float)
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command') as command, patch.object(p, 'emit') as emit:
            p.apply_layout(request, Path('/unused'))
            command.assert_not_called()
            self.assertEqual(emit.call_args.args[0], 'unchanged')
        for invalid in (True, 1.5, float('nan'), float('inf'), 1000001):
            request['outputs'][0]['x'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                p.validate_layout(request, self.screens)
        self.assertEqual(p.logical_size(display('DP-1', width=2560, height=1440, scale=1.75)), (1462, 822))

    def test_laptop_target_and_every_preset_keep_a_usable_display(self):
        with self.assertRaisesRegex(ValueError, 'built-in'):
            p.desired_preset(self.screens, 'laptop', 'HDMI-A-1', '')
        internal_only = [display('eDP-1'), display('DSI-1', False)]
        with self.assertRaisesRegex(ValueError, 'At least one'):
            p.desired_preset(internal_only, 'external', '', '')
        _, desired = p.desired_preset(self.screens, 'single', 'DP-1', '')
        self.assertEqual([o['name'] for o in desired if o['enabled']], ['DP-1'])
        self.assertEqual(desired[2]['position'], {'x': 0, 'y': 0})
        disabled = display('eDP-1', False)
        disabled.pop('position')
        _, desired = p.desired_preset([disabled, self.screens[1]], 'laptop', 'eDP-1', '')
        self.assertEqual(desired[0]['position'], {'x': 0, 'y': 0})
        for field, value in [('scale', 0), ('modes', []), ('scale', float('inf'))]:
            broken = copy.deepcopy(self.screens)
            broken[2][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                p.desired_preset(broken, 'extend', '', '')

    def test_change_during_dryrun_aborts_without_mutation_or_rollback(self):
        stale = copy.deepcopy(self.screens)
        stale[0]['scale'] = 2
        with patch.object(p, 'outputs', side_effect=[self.screens, stale]), \
             patch.object(p, 'command') as command, patch.object(p, 'restore') as restore:
            with self.assertRaisesRegex(ValueError, 'topology changed'):
                p.transaction('extend', '', '', Path('/unused'))
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0][0], '--dryrun')
            restore.assert_not_called()

    def test_cli_unique_controls_share_one_lock_and_settle_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ('projection-control-1', 'projection-control-2'):
                with patch.object(p.sys, 'argv', ['projection.py', 'apply', 'extend', '', '', str(directory / name)]), \
                     patch.object(p, 'transaction'), patch.object(p.fcntl, 'flock') as flock:
                    p.main()
                    self.assertEqual(flock.call_args.args[1], p.fcntl.LOCK_EX | p.fcntl.LOCK_NB)
            self.assertEqual([path.name for path in directory.iterdir()], ['projection.lock'])
            with patch.object(p.sys, 'argv', ['projection.py', 'settle', temp]), \
                 patch.object(p.fcntl, 'flock') as flock, patch.object(p, 'outputs', return_value=self.screens), \
                 patch('builtins.print') as output:
                p.main()
                self.assertEqual(flock.call_args.args[1], p.fcntl.LOCK_EX)
                self.assertEqual(len(json.loads(output.call_args.args[0])), 3)

    def test_restore_unplugged_external_only_recovers_survivor(self):
        snapshot = [display('HDMI-A-1', True), display('eDP-1', False)]
        snapshot[1].pop('position')
        with patch.object(p, 'outputs', return_value=[snapshot[1]]), patch.object(p, 'command') as command:
            p.restore(snapshot)
            args = command.call_args.args[0]
            self.assertIn('--on', args)
            self.assertIn('1920x1200@60.000000Hz', args)


if __name__ == '__main__':
    unittest.main()

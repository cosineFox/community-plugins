"""Exercise layout transactions without touching the user's Wayland session."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('projection', Path(__file__).resolve().parents[1] / 'projection.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def display(name, enabled=True, refresh=60.0):
    return dict(name=name, enabled=enabled, modes=[dict(width=1920, height=1200,
                refresh=refresh, current=enabled, preferred=refresh == 60)],
                position=dict(x=0, y=0), scale=1.25, transform='normal', adaptive_sync=False)


class Projection(unittest.TestCase):
    def setUp(self):
        self.screens = [display('HDMI-A-1', False), display('eDP-1', refresh=47)]

    def test_all_four_modes_keep_a_display_and_order_source_first(self):
        for mode in ('laptop', 'duplicate', 'extend', 'external'):
            args = p.layout(self.screens, mode, 'eDP-1', 'HDMI-A-1')
            self.assertEqual(args[:2], ['--output', 'eDP-1'])
            self.assertIn('--on', args)
            self.assertIn('--off', args) if mode in ('laptop', 'external') else self.assertNotIn('--off', args)
            if mode != 'external':
                self.assertIn('1920x1200@47.000000Hz', args)
            if mode in ('duplicate', 'extend'):
                self.assertEqual(args[-2:], ['--right-of', 'eDP-1'])

    def test_invalid_request_fails_before_any_command(self):
        with patch.object(p, 'command') as command:
            for mode, source, dest in [('oops', 'eDP-1', 'HDMI-A-1'),
                                       ('extend', 'missing', 'HDMI-A-1'),
                                       ('external', 'eDP-1', 'eDP-1'),
                                       ('duplicate', 'eDP-1', 'unplugged')]:
                with self.assertRaises(ValueError): p.layout(self.screens, mode, source, dest)
            command.assert_not_called()

    def test_restore_preserves_refresh_scale_rotation_and_disabled_state(self):
        self.screens[1].update(transform='90', position=dict(x=-1200, y=40))
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command') as command:
            p.restore(self.screens)
            args = command.call_args.args[0]
            self.assertEqual(args[:3], ['--output', 'HDMI-A-1', '--off'])
            for value in ['1920x1200@47.000000Hz', '1.25', '90', '-1200,40']:
                self.assertIn(value, args)

    def test_restore_can_recreate_a_custom_refresh_mode(self):
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command', side_effect=[RuntimeError('mode missing'), '', '']) as command:
            p.restore(self.screens)
            self.assertIn('--custom-mode', command.call_args.args[0])

    def test_unplugged_external_only_recovers_laptop(self):
        snapshot = [display('HDMI-A-1'), display('eDP-1', False)]
        with patch.object(p, 'outputs', return_value=[snapshot[1]]), patch.object(p, 'command') as command:
            p.restore(snapshot)
            command.assert_called_once_with(['--output', 'eDP-1', '--on', '--preferred'])

    def test_transaction_keep_revert_timeout_and_failed_apply(self):
        for decision in ('keep', 'revert', 'timeout', 'failed-apply'):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as temp:
                control = Path(temp) / 'control'
                control.write_text(decision)
                calls = []
                def command(args):
                    calls.append(args)
                    if decision == 'failed-apply' and len(calls) == 2: raise RuntimeError('failed')
                    return ''
                with patch.object(p, 'outputs', return_value=copy.deepcopy(self.screens)), patch.object(p, 'command', side_effect=command), patch.object(p, 'restore') as restore, patch.object(p, 'emit'):
                    if decision == 'failed-apply':
                        with self.assertRaises(RuntimeError): p.transaction('extend', 'eDP-1', 'HDMI-A-1', control)
                    else:
                        p.transaction('extend', 'eDP-1', 'HDMI-A-1', control, seconds=0 if decision == 'timeout' else 1)
                    if decision == 'keep': restore.assert_not_called()
                    else: restore.assert_called_once()

    def test_laptop_only_noop_does_not_write_outputs_or_start_timer(self):
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command') as command, patch.object(p, 'emit') as emit, patch.object(p, 'restore') as restore:
            p.transaction('laptop', 'eDP-1', 'HDMI-A-1', Path('/unused'))
            command.assert_not_called()
            restore.assert_not_called()
            emit.assert_called_once_with('unchanged', mode='laptop', source='eDP-1', destination='HDMI-A-1')

    def test_dryrun_rejection_does_not_change_outputs(self):
        with patch.object(p, 'outputs', return_value=self.screens), patch.object(p, 'command', side_effect=RuntimeError('rejected')) as command, patch.object(p, 'restore') as restore:
            with self.assertRaises(RuntimeError): p.transaction('extend', 'eDP-1', 'HDMI-A-1', Path('/unused'))
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0][0], '--dryrun')
            restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()

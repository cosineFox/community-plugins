# Wayland Screen Mirror

Project to an external display with Laptop only, Duplicate, Extend, or External
only. Mirroring uses the existing managed `wl-mirror` service. The original
source/destination mirroring panel remains available.

## Plugin

| Field | Value |
| --- | --- |
| ID | `elijaharch/wl-screen-mirror` |
| Entries | Bar widget: `mirror`; Control Center shortcut: `open-controls`; panels: `controls`, `projection`; services: `mirror-service`, `projection-service` |

## Requirements

- [`wl-mirror`](https://github.com/Ferdi265/wl-mirror) available on `PATH`
- [`wlr-randr`](https://gitlab.freedesktop.org/emersion/wlr-randr) and `python3`
  for the projection panel. Python uses only its standard library.
- A compositor supporting `wlr-output-management` for output layout changes,
  such as labwc. Compositors without this protocol can still use the original
  mirroring panel with already enabled outputs.
- A compositor exposing a capture protocol supported by `wl-mirror` and two
  enabled displays. Display detection alone does not guarantee capture support.

## Usage

1. Add **Project** to Settings → Control Center shortcuts (`open-controls`).
2. Select the laptop/source and external display, then choose a projection mode.
3. Click **Keep** within 15 seconds, or the previous layout is restored.
   Closing the panel does not confirm the change.

```sh
noctalia msg panel-toggle elijaharch/wl-screen-mirror:projection
```

| Mode | Result |
| --- | --- |
| Laptop only | Enables the selected source; disables other connected outputs. |
| Duplicate | Enables the selected pair side by side; starts `wl-mirror` after Keep. |
| Extend | Enables the selected pair with the external display to the right. |
| External only | Enables the selected external display; disables other outputs. |

Duplicate previews the output arrangement before confirmation, then starts the
capture process. A capture failure is reported by the original mirror service;
the Project panel displays that error directly. Select another mode to stop capture.
Duplicate is a fullscreen capture window, not compositor-native output cloning.

Laptop only is marked current and inactive when the selected source is already
the only enabled output. This does not start a layout transaction or a timer.

The original `mirror` bar widget opens the mirroring controls directly. Select
two already enabled displays and click **Start mirroring**, or use:

```sh
noctalia msg panel-toggle elijaharch/wl-screen-mirror:controls
```

The service stops mirroring automatically if either selected output disconnects.
The open panel refreshes its monitor lists when displays connect or disconnect.
The Control Center shortcut is highlighted while mirroring is running. It remains
clickable when setup is incomplete, so the panel can explain what is missing.

### Labwc shortcut

On Noctalia builds with **Projection Panel** in Settings → Control Center, choose
this plugin's `projection` panel there to embed its controls directly in Monitor.
That integration needs the companion core change; stock Noctalia 5.1.0 does not
provide the setting. No separate desktop launcher entry is needed.

The embedded view uses a compact two-column layout and the Monitor page's close
button. The same panel remains available separately through IPC if desired.

Inside the existing `<keyboard>` section of `~/.config/labwc/rc.xml`, bind an
unused key to the panel:

```xml
<keybind key="W-p">
  <action name="Execute" command="noctalia msg panel-toggle elijaharch/wl-screen-mirror:projection"/>
</keybind>
```

Run `labwc --reconfigure` to activate the binding, then press Super+P. Replace any
existing Super+P binding rather than adding a duplicate.

The projection panel includes connected but disabled displays. It preserves the
current modes of enabled displays and selects the preferred mode when enabling
a display without a current mode. Scale and rotation are preserved. Changes
are session-only; compositor startup configuration is never rewritten.

The rollback snapshot includes enabled state, mode/refresh, position, scale,
rotation, and adaptive sync. Disconnected outputs are omitted from recovery; if
the only previously enabled display disappears, a surviving laptop display is
enabled at its preferred mode. The helper restores unconfirmed changes on normal
termination signals. Forced SIGKILL, compositor crashes, and hardware failures
can prevent restoration. Layout tools that change outputs concurrently may
conflict with a pending preview; finish or revert the preview first.

## Development checks

Run the focused regression tests with Python 3 and the Luau interpreter installed:

```sh
python3 -m unittest discover -s wl-screen-mirror/tests -p 'test_*.py'
noctalia plugins lint wl-screen-mirror
```

Luau is a test tool only. Python 3 is also used by the projection helper.

## Notes

- The plugin launches
  `wl-mirror --fullscreen-output DESTINATION --fullscreen SOURCE`.
- A private marker and the latest `wl-mirror` error output are written under the
  plugin data directory. No user content is stored.
- The managed process is terminated when mirroring stops, the plugin reloads,
  or Noctalia exits. Other `wl-mirror` processes are not affected.
- The projection service runs `python3 projection.py list` when its panel opens
  or output geometry changes. Applying a mode runs a short-lived Python helper
  and `wlr-randr` queries, dry runs, apply, and (when needed) restore commands.
  There is no idle polling process. A control file and lock file live under the
  plugin data directory; no display snapshots are written to disk.
- The existing mirror wrapper uses `/bin/sh`, `sleep`, and shell built-ins for
  process control. The projection wrapper uses `/bin/sh` and `printf` to report
  unexpected helper exits. No new system service or root permission is needed.
- The plugin makes no network requests.

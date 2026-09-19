# Wayland Screen Mirror

Identify and arrange multiple Wayland displays, switch layouts safely, and mirror
a selected pair. Display management is opt-in and session-only: this plugin never
rewrites compositor startup configuration.

## Requirements

- The companion Noctalia core patch with **plugin API 33** (position canvas and
  output identifier overlays). Stock Noctalia 5.1.0 does not have these additions.
- `python3` and [`wlr-randr`](https://gitlab.freedesktop.org/emersion/wlr-randr), plus
  a compositor supporting `wlr-output-management`, such as labwc.
- [`wl-mirror`](https://github.com/Ferdi265/wl-mirror) and a supported capture
  protocol for mirroring. Layout management does not need a running mirror.

No toolkit, root access, daemon, or third-party Python module is added.

## Displays in Monitor

Select this plugin in Settings → Control Center → Projection Panel:

```toml
[control_center]
project_panel = "elijaharch/wl-screen-mirror:projection"
```

The compact Displays section appears above brightness controls. **Identify** shows
matching numbers and connector names on enabled screens for five seconds, without
stealing focus or intercepting clicks. Numbers are shared by the compact panel,
arranger, and overlays; moving a display does not change its number. They are
connector-based and stable within the shell session, not permanent hardware IDs.

**Arrange displays…** opens the larger floating panel. The existing Project
shortcut and panel ID are retained:

```sh
noctalia msg panel-toggle elijaharch/wl-screen-mirror:projection
```

The original `controls` panel and `mirror` widget still work independently.

## Arrangement

1. Identify the physical screens.
2. Select a numbered tile or display button. Disabled displays are selectable in
   the list but are not drawn in the active desktop canvas.
3. Drag active tiles, or enter X/Y coordinates and press Enter. Positions use
   logical desktop pixels, including scale and rotation. Negative coordinates
   and staggered layouts are supported; nearby edges snap and overlaps are rejected.
4. Enable a disabled screen at the desktop's right edge, disable an enabled
   screen, or choose **Use only this display**. At least one display must remain on.
5. **Apply arrangement** previews the draft. **Keep** accepts it within 15 seconds;
   **Revert** or timeout restores the previous layout. **Reset draft** only discards
   unsaved edits, with no output commands.

Dragging and selection alone do not change live outputs. A live topology change
invalidates an unsaved draft. Presets and mirroring are unavailable while a draft
needs Apply or Reset. Current layouts are detected from observed output and mirror
state rather than the last action clicked.

| Action | Result |
| --- | --- |
| Laptop only | Enables the built-in display and disables the others. |
| Extend all displays | Enables every connected display; preserves existing positions and adds newly enabled screens at the right edge. |
| External displays only | Enables every external display and disables built-in panels. |
| Mirror… | Reveals explicit source/destination selectors; unrelated displays are preserved. |

Mirroring supports **one pair** and uses a fullscreen `wl-mirror` capture window,
not compositor-native cloning. When a layout change is necessary, capture starts
only after Keep; an already suitable layout needs no countdown. Capture errors are
shown rather than reporting a successful mirror. Stop mirroring leaves the output
layout in place. A rejected/reverted layout attempts to resume the previous managed
pair when both outputs remain usable.

## Safety and limitations

- All connected outputs participate in the snapshot. Modes, refresh rates, scale,
  rotation, and adaptive sync are preserved unless an output must be enabled at
  its preferred mode.
- Requests are validated before mutation, dry-run first, rechecked for staleness,
  and verified from compositor readback. Keep rechecks the pending topology.
- Confirmation opens in the persistent arrangement panel. The companion core
  rehomes persistent panels when their output disappears. Closing a panel does
  not confirm a preview.
- Helpers share a lock but have unique control/request files, so a reloaded
  service cannot confirm an older transaction. Reload recovery waits for the
  previous helper to finish before attempting to resume a stopped mirror.
- If the previously enabled screens disconnect, recovery enables a surviving
  built-in display (or another surviving output). Forced SIGKILL, compositor
  crashes, and hardware failures can prevent restoration. Concurrent external
  layout tools should not be used during a preview.
- Geometry follows `wlr-randr` 0.5.0 and wlroots 0.20.2 logical-size truncation.
  Other compositor behaviour, multi-monitor scaling, physical Identify overlays,
  and panel migration require live hardware validation before deployment.
- No persistent profiles or multiple mirror groups are provided in this update.

## Development

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
/path/to/patched/noctalia plugins lint /path/to/wl-screen-mirror
```

Luau is needed only by the mocked entry tests. Tests do not issue display commands.
The helper runs for queries, short-lived layout previews, and reload recovery; it
has no idle polling process. During a preview it checks output state approximately
once per second and immediately before Keep.

The plugin data directory holds the shared lock, transaction-owned temporary
control/JSON request files (removed on normal completion, failure, or unload), and
the existing managed mirror marker/error output. Temporary arrangement requests
contain geometry, not user content. No profiles are saved and no network requests
are made. Workspace source changes do not deploy to the installed desktop.

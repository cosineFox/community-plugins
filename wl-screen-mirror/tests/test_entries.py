"""Run the shipped Luau entries against a small in-memory host double.

These tests execute the real panel/service/shortcut callbacks without launching
wl-mirror, changing outputs, or reading/writing the user's plugin state.
"""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

PLUGIN = pathlib.Path(__file__).resolve().parents[1]

HOST = r'''
local values, watchers = {}, {}
local monitors = {{name = "eDP-1"}}
local tree, renders = nil, 0
local shortcutState = {}
local toggled, request, streamCallback
local removed = {}
local files = {}
local function copy(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for k, v in pairs(value) do result[k] = copy(v) end
    return result
end
noctalia = {
    state = {
        get = function(k) return copy(values[k]) end,
        set = function(k, v)
            values[k] = copy(v)
            if k == "mirror_request" then request = copy(v) end
            for _, fn in ipairs(watchers[k] or {}) do fn(copy(v)) end
        end,
        watch = function(k, fn)
            watchers[k] = watchers[k] or {}
            table.insert(watchers[k], fn)
        end,
    },
    outputs = function() return monitors end,
    tr = function(k) return k end,
    commandExists = function() return true end,
    togglePanel = function(id) toggled = id end,
    pluginDataDir = function() return "/test-data" end,
    writeFile = function(p, v) files[p] = v; return true end,
    readFile = function(p) return files[p] end,
    removeFile = function(p) removed[p] = true; files[p] = nil; return true end,
    string = {trim = function(s) return s:match("^%s*(.-)%s*$") end},
    runStream = function(_, fn) streamCallback = fn; return true end,
    notify = function() end,
    notifyError = function() end,
}
ui = setmetatable({}, {__index = function(_, kind)
    return function(props, children) return {kind=kind, props=props, children=children or {}} end
end})
panel = {
    render = function(v) tree = v; renders += 1 end,
    close = function() end,
}
shortcut = {
    setLabel = function(v) shortcutState.label = v end,
    setIcon = function(v) shortcutState.icon = v end,
    setActive = function(v) shortcutState.active = v end,
    setEnabled = function(v) shortcutState.enabled = v end,
}
local function node(callback, current)
    current = current or tree
    if current.props.onChange == callback or current.props.onClick == callback then return current.props end
    for _, child in ipairs(current.children) do
        local found = node(callback, child)
        if found then return found end
    end
    return nil
end
'''


class Entries(unittest.TestCase):
    def run_luau(self, entries, assertions, setup=''):
        executable = shutil.which('luau')
        self.assertIsNotNone(executable, 'Install the Luau interpreter to run these tests')
        # Give each script its own local scope, as Noctalia does. Its host
        # callbacks remain accessible to the test after the script loads.
        scripts = '\n'.join('do\n' + (PLUGIN / name).read_text() + '\nend' for name in entries)
        with tempfile.TemporaryDirectory(prefix='screen-mirror-test-') as temp:
            program = pathlib.Path(temp) / 'test.luau'
            program.write_text(HOST + '\n' + setup + '\n' + scripts + '\n' + assertions)
            result = subprocess.run([executable, str(program)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shortcut_opens_panel_and_tracks_status(self):
        self.run_luau(['shortcut.luau'], '''
assert(shortcutState.enabled and not shortcutState.active)
onClick()
assert(toggled == "elijaharch/wl-screen-mirror:projection")
noctalia.state.set("mirror_status", {phase="running"})
assert(shortcutState.active)
for _, phase in ipairs({"starting", "stopping", "stopped", "error"}) do
    noctalia.state.set("mirror_status", {phase=phase})
    assert(not shortcutState.active and shortcutState.enabled)
end
noctalia.state.set("mirror_status", nil)
assert(not shortcutState.active and shortcutState.enabled)
''')

    def test_hotplug_updates_open_panel_and_blocks_same_display(self):
        self.run_luau(['panel.luau', 'service.luau'], '''
onOpen()
assert(not node("onToggleMirroring").enabled)
monitors = {{name="eDP-1"}, {name="HDMI-A-1"}}
onOutputsChanged()
assert(#node("onSourceChanged").options == 2)
assert(#node("onDestinationChanged").options == 1)
assert(node("onToggleMirroring").enabled)
onDestinationChanged(99, "invalid")
onToggleMirroring()
assert(request.source ~= request.destination)
assert(request.source == "eDP-1" and request.destination == "HDMI-A-1")
''')

    def test_unplug_while_stopped_and_reconnect_without_reopening(self):
        self.run_luau(['panel.luau', 'service.luau'], '''
monitors = {{name="eDP-1"}, {name="HDMI-A-1"}}
onOpen()
assert(node("onToggleMirroring").enabled)
monitors = {{name="eDP-1"}}
onOutputsChanged()
assert(not node("onToggleMirroring").enabled)
assert(#node("onDestinationChanged").options == 0)
monitors = {{name="DP-2"}, {name="eDP-1"}}
onOutputsChanged()
assert(node("onToggleMirroring").enabled)
onToggleMirroring()
assert(request.source == "eDP-1" and request.destination == "DP-2")
''')

    def test_hidden_panel_defers_render_until_open(self):
        self.run_luau(['panel.luau', 'service.luau'], '''
onOpen()
onClose()
local before = renders
monitors = {{name="eDP-1"}, {name="DP-1"}}
onOutputsChanged()
assert(renders == before)
onOpen()
assert(node("onToggleMirroring").enabled)
''')

    def test_active_mirror_stops_on_disconnect(self):
        self.run_luau(['panel.luau', 'service.luau'], '''
monitors = {{name="eDP-1"}, {name="HDMI-A-1"}}
onOpen()
onToggleMirroring()
assert(values.mirror_status.phase == "starting")
streamCallback("started")
assert(values.mirror_status.phase == "running")
removed = {}
monitors = {{name="eDP-1"}}
onOutputsChanged()
assert(values.mirror_status.phase == "stopping")
assert(removed["/test-data/mirror.run"])
streamCallback("stopped")
assert(values.mirror_status.phase == "stopped")
assert(not node("onToggleMirroring").enabled)
''')

    def test_projection_panel_four_modes_and_confirmation(self):
        self.run_luau(['projection-panel.luau'], '''
noctalia.state.set("projection_outputs", {{name="eDP-1"}})
noctalia.state.set("projection_status", {phase="idle", available=true})
onOpen()
assert(node("onLaptop").enabled and not node("onExternal").enabled)
noctalia.state.set("projection_outputs", {{name="HDMI-A-1", enabled=false}, {name="eDP-1"}})
assert(node("onDuplicate").enabled and node("onExtend").enabled and node("onExternal").enabled)
onDuplicate()
assert(values.projection_request.mode == "duplicate")
assert(values.projection_request.source == "eDP-1")
assert(values.projection_request.destination == "HDMI-A-1")
noctalia.state.set("projection_status", {phase="pending", available=true, seconds=9})
assert(not node("onLaptop").enabled and node("onKeep") and node("onRevert"))
onKeep()
assert(values.projection_request.action == "keep")
onRevert()
assert(values.projection_request.action == "revert")
''')

    def test_projection_panel_current_laptop_and_inline_mirror_error(self):
        self.run_luau(['projection-panel.luau'], '''
noctalia.state.set("projection_outputs", {{name="eDP-1", enabled=true}})
noctalia.state.set("projection_status", {phase="idle", available=true})
onOpen()
assert(not node("onLaptop").enabled)
assert(node("onLaptop").text == "projection.laptop_current")
assert(node("onMirrorControls") == nil)
onClose()
onOpen("embedded")
assert(node("onCloseClicked").visible == false)
assert(node("onLaptop").flexGrow == 1)
onLaptop()
assert(values.projection_request.action == "refresh")
noctalia.state.set("projection_outputs", {{name="eDP-1", enabled=true}, {name="HDMI-A-1", enabled=false}})
assert(not node("onLaptop").enabled)
noctalia.state.set("projection_outputs", {{name="eDP-1", enabled=true}, {name="HDMI-A-1", enabled=true}})
assert(node("onLaptop").enabled)
onLaptop()
assert(values.projection_request.mode == "laptop")
noctalia.state.set("projection_status", {phase="kept", mode="duplicate", available=true})
noctalia.state.set("mirror_status", {phase="error", message="Capture failed"})
assert(tree.children[#tree.children].props.text == "Capture failed")
''')

    def test_projection_service_waits_for_mirror_stop_then_confirms(self):
        self.run_luau(['projection-service.luau'], '''
noctalia.state.set("mirror_status", {phase="running"})
noctalia.state.set("projection_request", {action="apply", mode="duplicate", source="eDP-1", destination="HDMI-A-1"})
assert(values.mirror_request.action == "stop")
assert(streamCallback == nil)
noctalia.state.set("mirror_status", {phase="stopped"})
assert(streamCallback ~= nil and files["/test-data/projection.control"] == "pending")
noctalia.state.set("projection_request", {action="apply", mode="external", source="eDP-1", destination="HDMI-A-1"})
streamCallback({phase="pending", mode="duplicate", seconds=10})
noctalia.state.set("projection_request", {action="keep"})
assert(files["/test-data/projection.control"] == "keep")
streamCallback({phase="kept", mode="duplicate", source="eDP-1", destination="HDMI-A-1"})
assert(values.mirror_request.action == "start")
assert(values.mirror_request.destination == "HDMI-A-1")
assert(values.projection_status.phase == "kept")
streamCallback({phase="exited"})
assert(values.projection_status.phase == "kept")
''', setup='''
noctalia.pluginDir = function() return "/test-plugin" end
noctalia.json = {decode=function(v) return v end}
noctalia.runAsync = function(_, callback)
 callback({exitCode=0, stdout={{name="eDP-1"}, {name="HDMI-A-1"}}})
 return true
end
''')

    def test_projection_service_handles_helper_failure_and_unload(self):
        self.run_luau(['projection-service.luau'], '''
noctalia.state.set("projection_request", {action="apply", mode="laptop", source="eDP-1"})
assert(files["/test-data/projection.control"] == "pending")
streamCallback({phase="exited"})
assert(values.projection_status.phase == "error")
noctalia.state.set("projection_request", {action="apply", mode="laptop", source="eDP-1"})
assert(files["/test-data/projection.control"] == "pending")
onExit()
assert(files["/test-data/projection.control"] == nil)
''', setup='''
noctalia.pluginDir = function() return "/test-plugin" end
noctalia.json = {decode=function(v) return v end}
noctalia.runAsync = function() return false end
''')


if __name__ == '__main__':
    unittest.main()

"""Focused Luau model and entry tests with a no-process Noctalia host double."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

PLUGIN = pathlib.Path(__file__).resolve().parents[1]

HOST = r'''
local values, watchers, files = {}, {}, {}
local monitors, tree, request, streamCallback, identifiers, encoded, renders = {}, nil, nil, nil, nil, nil, 0
local shortcutState = {}
shortcut={setLabel=function(v) shortcutState.label=v end,setIcon=function(v) shortcutState.icon=v end,setActive=function(v) shortcutState.active=v end,setEnabled=function(v) shortcutState.enabled=v end}
local function copy(v) if type(v) ~= "table" then return v end local r = {}; for k,x in pairs(v) do r[k]=copy(x) end; return r end
noctalia = { state = {
 get=function(k) return copy(values[k]) end,
 set=function(k,v) values[k]=copy(v); if k=="projection_request" or k=="mirror_request" then request=copy(v) end; for _,f in ipairs(watchers[k] or {}) do f(copy(v)) end end,
 watch=function(k,f) watchers[k]=watchers[k] or {}; table.insert(watchers[k],f) end },
 tr=function(k) return k end, commandExists=function() return true end, pluginDataDir=function() return "/test-data" end,
 outputs=function() return monitors end, nowMs=function() return 1234 end,
 readFile=function(p) return files[p] end, notify=function() end, notifyError=function() end,
 string={trim=function(v) return v:match("^%s*(.-)%s*$") end},
 pluginDir=function() return "/test-plugin" end, writeFile=function(p,v) files[p]=v; return true end,
 removeFile=function(p) files[p]=nil; return true end, showOutputIdentifiers=function(v) identifiers=v; return true end,
 togglePanel=function() end, json={decode=function(v) return v end, encode=function(v) encoded=copy(v); return "{}" end},
 runAsync=function(_,cb) cb({exitCode=0,stdout=monitors}); return true end,
 runStream=function(_,cb) streamCallback=cb; return true end }
ui=setmetatable({}, {__index=function(_,kind) return function(props,children) return {kind=kind,props=props,children=children or {}} end end})
panel={render=function(v) tree=v; renders+=1 end,close=function() end}
local function node(callback, current)
 current=current or tree; if current and current.props and (current.props.onClick==callback or current.props.onChange==callback or current.props.onSubmit==callback) then return current.props end
 if current then for _,child in ipairs(current.children or {}) do local hit=node(callback,child); if hit then return hit end end end
end
local function control(kind, current)
 current=current or tree
 if current.kind==kind then return current.props end
 for _,child in ipairs(current.children or {}) do local found=control(kind,child); if found then return found end end
end
local function lastLabel(current)
 current=current or tree
 local found=current.kind=="label" and current.props.text or nil
 for _,child in ipairs(current.children or {}) do found=lastLabel(child) or found end
 return found
end
local function button(text, current)
 current=current or tree
 if current.kind=="button" and current.props.text==text then return current.props end
 for _,child in ipairs(current.children or {}) do local found=button(text,child); if found then return found end end
end
'''

class Entries(unittest.TestCase):
    def run_luau(self, entries, assertions, setup=''):
        luau = shutil.which('luau')
        self.assertIsNotNone(luau, 'Luau interpreter is required')
        module = (PLUGIN / 'display-model.luau').read_text()
        loader = 'local displayModel = (function()\n' + module + '\nend)()\nrequire=function(_) return displayModel end\n'
        scripts = '\n'.join('do\n' + (PLUGIN / name).read_text() + '\nend' for name in entries)
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / 'test.luau'
            path.write_text(HOST + loader + setup + scripts + assertions)
            result = subprocess.run([luau, str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_model_stable_numbering_fingerprint_and_snap_rejection(self):
        self.run_luau([], r'''
local a={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100}, {name="DP-5",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100}}
local numbers,nextNumber=displayModel.number(a,{},1)
local reordered={{name="DP-5",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100},a[1]}
numbers,nextNumber=displayModel.number(reordered,numbers,nextNumber)
assert(numbers["eDP-1"]==1 and numbers["DP-5"]==2)
assert(displayModel.fingerprint(a)==displayModel.fingerprint(reordered))
local draft=displayModel.draft(a)
local moved,reason=displayModel.snap(draft,"DP-5",0,0)
assert(moved==nil and reason=="projection.overlap")
''')

    def test_full_panel_draft_apply_reset_and_hotplug_invalidation(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100,scale=1}, {name="DP-5",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100,scale=1}, {name="HDMI-A-1",enabled=false,logical_width=100,logical_height=100,scale=1}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); onOpen()
onCanvasMove("DP-5",200,0); assert(node("onApplyLayout").enabled)
onApplyLayout(); assert(values.projection_request.action=="apply_layout" and #values.projection_request.layout.outputs==3)
onReset(); assert(not node("onApplyLayout").enabled)
onCanvasMove("DP-5",200,0); monitors[2].position.x=220; noctalia.state.set("projection_outputs",monitors)
assert(lastLabel()=="projection.topology_changed")
''')

    def test_embedded_actions_identify_and_mirror_selectors(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100}, {name="DP-5",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100}, {name="HDMI-A-1",enabled=true,position={x=200,y=0},logical_width=100,logical_height=100}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); onOpen("embedded")
assert(node("onSourceChanged")==nil); onIdentify(); assert(#identifiers==3 and identifiers[2].label=="2")
assert(node("onArrange").enabled)
onExtend(); assert(values.projection_request.action=="refresh")
onToggleMirror(); assert(node("onSourceChanged") and node("onDestinationChanged")); onDuplicate(); assert(values.projection_request.mode=="duplicate")
''')

    def test_service_serializes_layout_and_restores_prior_pair_after_revert(self):
        self.run_luau(['projection-service.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100}, {name="DP-5",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100}, {name="HDMI-A-1",enabled=true,position={x=200,y=0},logical_width=100,logical_height=100}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("mirror_status",{phase="running",source="DP-5",destination="HDMI-A-1"})
noctalia.state.set("projection_request",{action="apply_layout",layout={baseline=monitors,outputs={{name="eDP-1",enabled=true,x=0,y=0},{name="DP-5",enabled=true,x=100,y=0},{name="HDMI-A-1",enabled=true,x=200,y=0}}}})
assert(values.mirror_request.action=="stop"); noctalia.state.set("mirror_status",{phase="stopped"}); assert(streamCallback)
streamCallback({phase="pending",mode="layout"}); noctalia.state.set("projection_request",{action="revert"}); local control; for path, value in pairs(files) do if string.find(path,"projection-control-",1,true) then control=path end end; assert(control and files[control]=="revert")
streamCallback({phase="reverted",mode="layout"}); assert(values.mirror_request.action=="start" and values.mirror_request.source=="DP-5")
''')

    # Restored coverage from the upstream entry suite, retained alongside the
    # projection-specific regressions above.
    def test_shortcut_opens_panel_and_tracks_status(self):
        self.run_luau(['shortcut.luau'], r'''
assert(shortcutState.enabled and not shortcutState.active)
local toggled; noctalia.togglePanel=function(id) toggled=id end
onClick(); assert(toggled=="elijaharch/wl-screen-mirror:projection")
noctalia.state.set("mirror_status",{phase="running"}); assert(shortcutState.active)
for _,phase in ipairs({"starting","stopping","stopped","error"}) do
 noctalia.state.set("mirror_status",{phase=phase}); assert(not shortcutState.active and shortcutState.enabled)
end
''')

    def test_projection_current_presets_and_mirror_updates(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100},{name="DP-1",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); noctalia.state.set("mirror_status",{phase="stopped"}); onOpen()
assert(node("onExtend").selected and node("onExtend").text=="projection.extend · projection.current")
noctalia.state.set("mirror_status",{phase="running",source="eDP-1",destination="DP-1"}); assert(not node("onExtend").selected)
onToggleMirror(); assert(node("onStopMirroring")); noctalia.state.set("mirror_status",{phase="error",message="capture failed"}); assert(lastLabel()=="capture failed")
''')

    def test_disabled_outputs_stay_selectable_and_invalid_input_keeps_draft_valid(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100},{name="DP-1",enabled=false,logical_width=100,logical_height=100}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); onOpen()
assert(#control("positionCanvas").items==1)
button("2 · DP-1 · projection.disabled").onClick(); assert(node("onToggleEnabled").text=="projection.enable")
onToggleEnabled(); assert(node("onApplyLayout").enabled)
onCanvasMove("DP-1",math.huge,0); assert(node("onApplyLayout").enabled)
local key = node("onX").key
onX("not a coordinate"); assert(node("onApplyLayout").enabled and node("onX").key ~= key)
assert(#control("positionCanvas").items==2 and control("positionCanvas").items[2].x==100)
''')

    def test_baseline_refresh_and_fingerprint_mode_changes_discard_draft(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100,modes={{current=true,width=100,height=100,refresh=60}}},{name="DP-1",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100,modes={{current=true,width=100,height=100,refresh=60}}}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); onOpen(); onCanvasMove("DP-1",200,0)
monitors[2].modes[1].refresh=75; noctalia.state.set("projection_outputs",monitors)
assert(lastLabel()=="projection.topology_changed")
''')

    def test_projection_service_uses_unique_owned_files_and_ignores_old_callbacks(self):
        self.run_luau(['projection-service.luau'], r'''
monitors={{name="eDP-1",enabled=true},{name="DP-1",enabled=true}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_request",{action="apply",mode="duplicate",source="eDP-1",destination="DP-1"})
local first=streamCallback; local control; for path,_ in pairs(files) do if string.find(path,"projection-control-",1,true) then control=path end end; assert(control)
first({phase="error",message="failed"}); assert(files[control]==nil)
noctalia.state.set("projection_request",{action="apply",mode="duplicate",source="eDP-1",destination="DP-1"}); local second=streamCallback; first({phase="pending"}); assert(values.projection_status.phase=="applying")
second({phase="error",message="failed again"}); assert(values.projection_status.phase=="error")
''')

    def test_original_mirror_panel_hotplug_same_display_and_reopen(self):
        self.run_luau(['panel.luau', 'service.luau'], r'''
monitors={{name="eDP-1"}}; onOpen(); assert(not node("onToggleMirroring").enabled)
monitors={{name="eDP-1"},{name="DP-1"}}; onOutputsChanged()
assert(#node("onSourceChanged").options==2 and #node("onDestinationChanged").options==1)
onDestinationChanged(99); onToggleMirroring()
assert(values.mirror_request.source=="eDP-1" and values.mirror_request.destination=="DP-1")
streamCallback("started"); assert(values.mirror_status.phase=="running")
monitors={{name="eDP-1"}}; onOutputsChanged()
assert(values.mirror_status.phase=="stopping" and files["/test-data/mirror.run"]==nil)
streamCallback("stopped"); assert(not node("onToggleMirroring").enabled)
onClose(); local before=renders
monitors={{name="DP-2"},{name="eDP-1"}}; onOutputsChanged(); assert(renders==before)
onOpen(); assert(node("onToggleMirroring").enabled)
''')

    def test_laptop_target_independent_of_mirror_source_and_disabled_laptop(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=false,logical_width=100,logical_height=100},{name="DP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100},{name="DP-2",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); onOpen("embedded")
onToggleMirror(); onSourceChanged(1); onLaptop(); assert(values.projection_request.source=="eDP-1")
assert(node("onExternal").selected and not node("onExternal").enabled)
monitors={{name="eDP-1",enabled=true},{name="DSI-1",enabled=false}}
noctalia.state.set("projection_outputs",monitors); assert(node("onExternal")==nil)
''')

    def test_current_returned_draft_and_helper_error_reset(self):
        self.run_luau(['projection-panel.luau'], r'''
monitors={{name="eDP-1",enabled=true,position={x=0,y=0},logical_width=100,logical_height=100},{name="DP-1",enabled=true,position={x=100,y=0},logical_width=100,logical_height=100}}
noctalia.state.set("projection_outputs",monitors); noctalia.state.set("projection_status",{phase="idle",available=true}); onOpen()
monitors[2].position.x=200; noctalia.state.set("projection_outputs",monitors)
onCanvasMove("DP-1",300,0); onApplyLayout(); assert(values.projection_request.layout.baseline[2].position.x==200)
noctalia.state.set("projection_status",{phase="error",available=true,message="Rejected"})
assert(not node("onApplyLayout").enabled and control("positionCanvas").items[2].x==200)
onCanvasMove("DP-1",300,0); assert(node("onApplyLayout").enabled)
onCanvasMove("DP-1",200,0); assert(not node("onApplyLayout").enabled)
onCanvasSelect("DP-1"); onUseOnlySelected(); onApplyLayout()
assert(not values.projection_request.layout.outputs[1].enabled and values.projection_request.layout.outputs[2].enabled)
''')

    def test_service_cleans_every_terminal_and_failed_launch(self):
        self.run_luau(['projection-service.luau'], r'''
monitors={{name="eDP-1",enabled=false},{name="DP-1",enabled=true}}
noctalia.state.set("projection_outputs",monitors)
for _,phase in ipairs({"kept","reverted","unchanged","error"}) do
 noctalia.state.set("projection_request",{action="apply_layout",layout={baseline=monitors,outputs={}}})
 assert(next(files)~=nil and encoded.baseline[2].name=="DP-1")
 streamCallback({phase=phase,mode="layout"})
 assert(next(files)==nil and values.projection_recovery==nil)
end
noctalia.runStream=function() return false end
noctalia.state.set("projection_request",{action="apply",mode="laptop",source="eDP-1"})
assert(values.projection_status.phase=="error" and next(files)==nil)
''')

    def test_service_ignores_old_refresh_without_stalling_new_refresh(self):
        self.run_luau(['projection-service.luau'], r'''
noctalia.state.set("projection_outputs",{{name="eDP-1",enabled=true},{name="DP-1",enabled=false}})
noctalia.state.set("projection_request",{action="apply",mode="extend"})
streamCallback({phase="kept",mode="extend"}); assert(#callbacks==2)
callbacks[1]({exitCode=0,stdout={{name="old",enabled=true}}})
assert(values.projection_outputs[1].name=="eDP-1")
callbacks[2]({exitCode=0,stdout={{name="eDP-1",enabled=true},{name="DP-1",enabled=true}}})
assert(values.projection_outputs[2].enabled and values.projection_status.phase=="kept")
noctalia.state.set("projection_request",{action="refresh"}); assert(#callbacks==3)
''', setup=r'''
local callbacks={}
noctalia.runAsync=function(_,cb) table.insert(callbacks,cb); return true end
''')

    def test_service_reload_waits_for_old_helper_and_restores_pair(self):
        self.run_luau(['projection-service.luau'], r'''
assert(string.find(commands[1]," settle ",1,true) and files["old-control"]==nil)
noctalia.state.set("projection_request",{action="apply",mode="extend"}); assert(streamCallback==nil)
callbacks[1]({exitCode=0,stdout={{name="eDP-1",enabled=true},{name="DP-1",enabled=true}}})
assert(values.projection_recovery==nil and values.mirror_request.action=="start")
assert(values.mirror_request.source=="eDP-1" and values.mirror_request.destination=="DP-1")
''', setup=r'''
local commands,callbacks={},{}
values.projection_recovery={priorMirror={source="eDP-1",destination="DP-1"},ownedpaths={control="old-control",request="old-request"}}
files["old-control"]="pending"; files["old-request"]="{}"
noctalia.runAsync=function(cmd,cb) table.insert(commands,cmd); table.insert(callbacks,cb); return true end
''')

    def test_confirmation_footer_is_outside_the_scroll_view(self):
        self.run_luau(['projection-panel.luau'], r'''
noctalia.state.set("projection_outputs",{{name="eDP-1",enabled=true,logical_width=100,logical_height=100}})
noctalia.state.set("projection_status",{phase="pending",available=true,seconds=15}); onOpen()
assert(tree.kind=="column" and tree.children[1].kind=="scroll")
assert(tree.children[2].children[2].children[1].props.onClick=="onKeep")
assert(not node("onToggleEnabled").enabled)
''')

    def test_confirmation_opens_once_and_never_confirms_on_close(self):
        self.run_luau(['projection-service.luau'], r'''
monitors={{name="eDP-1",enabled=true},{name="DP-1",enabled=false}}
noctalia.state.set("projection_outputs",monitors)
local opened=0; noctalia.togglePanel=function(id) assert(id=="elijaharch/wl-screen-mirror:projection"); opened+=1 end
noctalia.state.set("projection_request",{action="apply",mode="extend"})
streamCallback({phase="pending",mode="extend",seconds=15}); assert(opened==1)
noctalia.state.set("projection_panel_open",false)
streamCallback({phase="pending",mode="extend",seconds=14}); assert(opened==1)
for _,value in pairs(files) do assert(value=="pending") end
onExit(); assert(next(files)==nil and values.projection_recovery~=nil)
''')

if __name__ == '__main__': unittest.main()

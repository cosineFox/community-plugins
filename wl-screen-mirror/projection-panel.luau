--!nonstrict

local model = require("./display-model.luau")
local opened, embedded, mirrorOpen = false, false, false
local outputs, draft, baseline = {}, nil, nil
local selectedId, source, destination, status = nil, nil, nil, noctalia.state.get("projection_status") or { phase = "idle", available = false }
local numbers, nextNumber, serial, dirty, invalidReason, warning, inputRevision = {}, 1, 0, false, nil, nil, 0
local submitted = false

local function builtin()
	for _, output in ipairs(outputs) do if model.isInternal(output.name) then return output.name end end
end

local function request(action, fields)
	serial += 1
	fields = fields or {}
	fields.action, fields.serial = action, serial
	noctalia.state.set("projection_request", fields)
end

local function mirror()
	return noctalia.state.get("mirror_status") or { phase = "stopped" }
end

local function enabledOutputs()
	return model.enabled(outputs)
end

local function chooseOutputs(value)
	outputs = type(value) == "table" and value or {}
	-- Number allocation is service-owned so both panel instances share connector labels.
	local shared = noctalia.state.get("projection_numbers") or { values = {}, next = 1 }
	numbers, nextNumber = model.number(outputs, shared.values, shared.next)
	local found = model.find(outputs, selectedId)
	if not found then selectedId = outputs[1] and outputs[1].name end
	if not model.find(outputs, source) then source = builtin() or (outputs[1] and outputs[1].name) end
	if not model.find(outputs, destination) or destination == source then
		destination = nil
		for _, output in ipairs(outputs) do if output.name ~= source then destination = output.name; break end end
	end
end

local function busy() return status.phase == "pending" or status.phase == "applying" end
local function currentDraft() return draft or model.draft(outputs) end
local function setDraft(value)
	draft, warning = value, nil
	dirty = not model.sameLayout(draft, model.draft(outputs))
	invalidReason = select(2, model.validDraft(draft))
	inputRevision += 1
end

local function presetCurrent(mode)
	local active, mirrorStatus = enabledOutputs(), mirror()
	local mirroring = mirrorStatus.phase == "running" or mirrorStatus.phase == "starting" or mirrorStatus.phase == "stopping"
	if mirroring then return false end
	if mode == "laptop" then return #active == 1 and model.isInternal(active[1].name) end
	if mode == "extend" then return #active == #outputs and #outputs > 0 end
	return false
end

local function isExternalCurrent()
	local active, externalCount = enabledOutputs(), 0
	for _, output in ipairs(outputs) do if not model.isInternal(output.name) then externalCount += 1 end end
	if mirror().phase == "running" or mirror().phase == "starting" or mirror().phase == "stopping" then return false end
	if #active == 0 or #active ~= externalCount then return false end
	for _, output in ipairs(active) do if model.isInternal(output.name) then return false end end
	return true
end

local function actionButton(mode, glyph, allowed, current)
	local text = noctalia.tr("projection." .. mode)
	if current then text = text .. " · " .. noctalia.tr("projection.current") end
	return ui.button({ text = text, glyph = glyph, variant = "secondary", flexGrow = embedded and 1 or nil,
		controlSize = embedded and "sm" or "md", selected = current,
		enabled = status.available and allowed and not busy() and not dirty and not current,
		onClick = "on" .. string.upper(string.sub(mode, 1, 1)) .. string.sub(mode, 2) })
end

local function selector(selected, callback, exclude)
	local labels, selectedIndex = {}, -1
	for _, output in ipairs(outputs) do if output.name ~= exclude then
		if output.name == selected then selectedIndex = #labels end
		table.insert(labels, tostring(numbers[output.name]) .. " · " .. output.name)
	end end
	return ui.select({ options = labels, selectedIndex = selectedIndex, placeholder = noctalia.tr("panel.no_monitor"), enabled = not busy() and not dirty, onChange = callback })
end

local function statusText()
	if status.phase == "pending" then return noctalia.tr("projection.pending", { seconds = status.seconds or 15 }) end
	if status.phase == "applying" then return noctalia.tr("projection.applying") end
	if status.phase == "error" then return status.message or noctalia.tr("projection.query_failed") end
	if mirror().phase == "error" then return mirror().message or noctalia.tr("projection.query_failed") end
	if not status.available then return noctalia.tr("projection.missing") end
	if dirty then return noctalia.tr("projection.dirty_actions_disabled") end
	if mirror().phase == "starting" then return noctalia.tr("projection.mirror_starting") end
	if status.phase == "kept" or status.phase == "reverted" or status.phase == "unchanged" then return noctalia.tr("projection." .. status.phase) end
	return noctalia.tr("projection.hint")
end

local function presetRow()
	local hasBuiltin, hasExternal = false, false
	for _, output in ipairs(outputs) do if model.isInternal(output.name) then hasBuiltin = true else hasExternal = true end end
	local buttons = {}
	if hasBuiltin and #outputs > 1 then table.insert(buttons, actionButton("laptop", "device-laptop", true, presetCurrent("laptop"))) end
	if hasBuiltin and hasExternal then table.insert(buttons, actionButton("external", "device-desktop", true, isExternalCurrent())) end
	local rows = {}
	if #buttons > 0 then table.insert(rows, ui.row({ gap = 6 }, buttons)) end
	if #outputs > 1 then table.insert(rows, actionButton("extend", "devices", true, presetCurrent("extend"))) end
	return #rows > 0 and ui.column({ gap = 6 }, rows) or nil
end

local function mirrorControls()
	if #outputs < 2 then return nil end
	local running = mirror().phase == "running" or mirror().phase == "starting"
	return ui.column({ gap = 5 }, {
		ui.label({ text = noctalia.tr("projection.source"), color = "on_surface_variant" }), selector(source, "onSourceChanged", nil),
		ui.label({ text = noctalia.tr("projection.destination"), color = "on_surface_variant" }), selector(destination, "onDestinationChanged", source),
		ui.button({ text = noctalia.tr(running and "projection.stop_mirroring" or "projection.duplicate"), glyph = "screen-share", enabled = status.available and not busy() and not dirty and (running or destination ~= nil), onClick = running and "onStopMirroring" or "onDuplicate" }),
	})
end

local function render()
	if not opened then return end
	local active, displayButtons = {}, {}
	for _, item in ipairs(currentDraft()) do
		if item.enabled then table.insert(active, { id = item.name, label = tostring(numbers[item.name]) .. "\n" .. item.name, x = item.x, y = item.y, width = item.width, height = item.height }) end
		local name = item.name
		table.insert(displayButtons, ui.button({ key = name, text = tostring(numbers[name]) .. " · " .. name .. (item.enabled and "" or " · " .. noctalia.tr("projection.disabled")), selected = selectedId == name, enabled = not busy(), onClick = function() onCanvasSelect(name) end }))
	end
	local children = { ui.row({ justify = "space_between", align = "center" }, {
		ui.label({ text = noctalia.tr("projection.title"), fontSize = 18, fontWeight = "bold" }), ui.button({ glyph = "close", variant = "ghost", visible = not embedded, onClick = "onCloseClicked" }),
	}) }
	local presets = presetRow(); if presets then table.insert(children, presets) end
	table.insert(children, ui.label({ text = noctalia.tr("projection.summary", { active = #enabledOutputs(), total = #outputs }) }))
	if mirror().phase == "running" then table.insert(children, ui.label({ text = noctalia.tr("projection.mirror_pair", { source = mirror().source or "?", destination = mirror().destination or "?" }) })) end
	if embedded then table.insert(children, ui.button({ text = noctalia.tr("projection.arrange"), glyph = "layout-grid", enabled = #outputs > 0 and not busy(), onClick = "onArrange" })) end
	if not embedded then
		table.insert(children, ui.positionCanvas({ items = active, selectedId = selectedId, enabled = status.available and not busy(), height = 300, onSelect = "onCanvasSelect", onMove = "onCanvasMove" }))
		table.insert(children, ui.column({ gap = 4 }, displayButtons))
		local selected = model.find(currentDraft(), selectedId)
		if selected then table.insert(children, ui.label({ text = tostring(numbers[selected.name]) .. " · " .. selected.name .. " · " .. selected.mode .. " · " .. tostring(selected.scale) .. "×" }))
			table.insert(children, ui.row({ gap = 6 }, {
				ui.input({ key = "x-" .. selected.name .. "-" .. inputRevision, value = tostring(selected.x), enabled = not busy(), placeholder = "X", onSubmit = "onX" }),
				ui.input({ key = "y-" .. selected.name .. "-" .. inputRevision, value = tostring(selected.y), enabled = not busy(), placeholder = "Y", onSubmit = "onY" }),
				ui.button({ text = selected.enabled and noctalia.tr("projection.disable") or noctalia.tr("projection.enable"), enabled = not busy() and (not selected.enabled or #active > 1), onClick = "onToggleEnabled" }),
			}))
			table.insert(children, ui.button({ text = noctalia.tr("projection.use_only_selected"), enabled = not busy() and status.available and (#active > 1 or not selected.enabled), onClick = "onUseOnlySelected" }))
		end
		table.insert(children, ui.row({ gap = 8 }, { ui.button({ text = noctalia.tr("projection.apply"), variant = "primary", enabled = status.available and dirty and invalidReason == nil and not busy(), onClick = "onApplyLayout" }), ui.button({ text = noctalia.tr("projection.reset"), enabled = dirty and not busy(), onClick = "onReset" }) }))
	end
	table.insert(children, ui.button({ text = noctalia.tr("projection.identify"), glyph = "numbers", enabled = #active > 0 and not busy(), onClick = "onIdentify" }))
	table.insert(children, ui.button({ text = noctalia.tr("projection.mirror"), glyph = "screen-share", enabled = #outputs > 1 and not busy() and not dirty, onClick = "onToggleMirror" }))
	if mirrorOpen then local controls = mirrorControls(); if controls then table.insert(children, controls) end end
	local footer = { ui.label({ text = warning and noctalia.tr(warning) or invalidReason and noctalia.tr(invalidReason) or statusText(), maxLines = 3, color = (warning or invalidReason or status.phase == "error") and "error" or "on_surface_variant" }) }
	if status.phase == "pending" then table.insert(footer, ui.row({ gap = 8 }, { ui.button({ text = noctalia.tr("projection.keep"), variant = "primary", onClick = "onKeep" }), ui.button({ text = noctalia.tr("projection.revert"), onClick = "onRevert" }) })) end
	if embedded then
		-- The Monitor tab already supplies a scroll viewport.
		for _, child in ipairs(footer) do table.insert(children, child) end
		panel.render(ui.column({ gap = 10 }, children))
	else
		-- Keep the countdown and decision controls visible even on small screens.
		panel.render(ui.column({ gap = 10 }, {
			ui.scroll({ gap = 10, padding = 4, flexGrow = 1 }, children),
			ui.column({ gap = 8 }, footer),
		}))
	end
end

noctalia.state.watch("mirror_status", function() render() end)
noctalia.state.watch("projection_status", function(value)
	if type(value) == "table" then
		status = value
		if value.phase == "kept" or value.phase == "reverted" or value.phase == "unchanged" or value.phase == "error" then
			if submitted or not dirty then
				draft, baseline, invalidReason, warning = model.draft(outputs), model.copy(outputs), nil, nil
				dirty, submitted = false, false
				inputRevision += 1
			end
		end
		render()
	end
end)
noctalia.state.watch("projection_outputs", function(value)
	local old = model.fingerprint(outputs); chooseOutputs(value); local changed = old ~= "" and old ~= model.fingerprint(outputs)
	if changed and dirty then draft, dirty, invalidReason, warning = nil, false, nil, "projection.topology_changed"; baseline = model.copy(outputs); inputRevision += 1
	elseif not dirty then draft, baseline, invalidReason, warning = model.draft(outputs), model.copy(outputs), nil, nil; inputRevision += 1 end
	render()
end)

function onOpen(context) opened, embedded = true, context == "embedded"; if not embedded then noctalia.state.set("projection_panel_open", true) end; status = noctalia.state.get("projection_status") or status; chooseOutputs(noctalia.state.get("projection_outputs")); if not dirty then draft, baseline = model.draft(outputs), model.copy(outputs) end; render(); request("refresh") end
function onClose() if opened and not embedded then noctalia.state.set("projection_panel_open", false) end; opened = false end
function onExit() onClose() end
function onCloseClicked() panel.close() end
function onArrange() noctalia.togglePanel("elijaharch/wl-screen-mirror:projection") end
function onToggleMirror() mirrorOpen = not mirrorOpen; render() end
function onIdentify() local items = {}; for _, output in ipairs(enabledOutputs()) do table.insert(items, { output = output.name, label = tostring(numbers[output.name]) }) end; noctalia.showOutputIdentifiers(items, 5000) end
function onKeep() request("keep") end
function onRevert() request("revert") end
function onLaptop() if not busy() and not dirty and builtin() and not presetCurrent("laptop") then request("apply", { mode = "laptop", source = builtin() }) end end
function onExtend() if not busy() and not dirty and not presetCurrent("extend") then request("apply", { mode = "extend", source = source or "" }) end end
function onExternal() if not busy() and not dirty and not isExternalCurrent() then request("apply", { mode = "external", source = builtin() or "" }) end end
function onDuplicate() if not dirty and destination then request("apply", { mode = "duplicate", source = source, destination = destination }) end end
function onStopMirroring() noctalia.state.set("mirror_request", { action = "stop" }) end
function onCanvasSelect(id) if not busy() and model.find(currentDraft(), id) then selectedId = id; render() end end
function onCanvasMove(id, x, y) local value, reason = model.snap(currentDraft(), id, tonumber(x), tonumber(y)); if value then setDraft(value) else warning = reason; inputRevision += 1 end; render() end
function onX(value) local item = model.find(currentDraft(), selectedId); if item then onCanvasMove(item.name, tonumber(value), item.y) end end
function onY(value) local item = model.find(currentDraft(), selectedId); if item then onCanvasMove(item.name, item.x, tonumber(value)) end end
function onToggleEnabled()
	local value, item = model.copy(currentDraft()), nil; item = model.find(value, selectedId)
	if item then
		item.enabled = not item.enabled
		if item.enabled then
			local right = 0; for _, other in ipairs(value) do if other.name ~= item.name and other.enabled then right = math.max(right, other.x + other.width) end end
			item.x, item.y = right, 0
		end
		local ok, reason = model.validDraft(value); if ok then setDraft(value) else warning = reason end; render()
	end
end
function onReset() draft, dirty, invalidReason, warning, baseline = model.draft(outputs), false, nil, nil, model.copy(outputs); inputRevision += 1; render() end
function onApplyLayout() if not busy() and dirty and invalidReason == nil then submitted = true; request("apply_layout", { layout = model.request(baseline or outputs, currentDraft()) }) end end
function onUseOnlySelected()
	if busy() then return end
	local value = model.copy(currentDraft())
	for _, item in ipairs(value) do
		if item.name == selectedId and not item.enabled then item.x, item.y = 0, 0 end
		item.enabled = item.name == selectedId
	end
	local ok, reason = model.validDraft(value)
	if ok then setDraft(value) else warning = reason end
	render()
end
function onSourceChanged(index)
	local candidate = outputs[(tonumber(index) or -1) + 1]
	if candidate then source = candidate.name; chooseOutputs(outputs); render() end
end
function onDestinationChanged(index) local candidates = {}; for _, output in ipairs(outputs) do if output.name ~= source then table.insert(candidates, output) end end; destination = candidates[(tonumber(index) or -1) + 1] and candidates[(tonumber(index) or -1) + 1].name or destination; render() end

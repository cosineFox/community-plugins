--!nonstrict

local opened = false
local embedded = false
local serial = 0
local outputs = {}
local source = nil
local destination = nil
local status = noctalia.state.get("projection_status") or {phase = "idle", available = false}
local render

local function laptopOnlyActive()
	local active = false
	for _, output in ipairs(outputs) do
		if output.enabled then
			if output.name ~= source then return false end
			active = true
		end
	end
	local mirror = noctalia.state.get("mirror_status") or {}
	return active and mirror.phase ~= "running" and mirror.phase ~= "starting" and mirror.phase ~= "stopping"
end

local function request(action, mode)
	serial += 1
	noctalia.state.set("projection_request", {
		action = action, mode = mode, source = source, destination = destination, serial = serial,
	})
end

local function selectOutputs(value)
	outputs = type(value) == "table" and value or {}
	local sourceFound, destinationFound = false, false
	for _, output in ipairs(outputs) do
		if output.name == source then sourceFound = true end
		if output.name == destination then destinationFound = true end
	end
	if not sourceFound then
		source = nil
		for _, output in ipairs(outputs) do
			if string.match(output.name, "^eDP%-") or string.match(output.name, "^LVDS%-") or string.match(output.name, "^DSI%-") then
				source = output.name
				break
			end
		end
		source = source or (outputs[1] and outputs[1].name)
	end
	if not destinationFound or destination == source then
		destination = nil
		for _, output in ipairs(outputs) do
			if output.name ~= source then destination = output.name; break end
		end
	end
end

local function selector(selected, callback, exclude)
	local labels, index = {}, -1
	for _, output in ipairs(outputs) do
		if output.name ~= exclude then
			if output.name == selected then index = #labels end
			table.insert(labels, output.name)
		end
	end
	return ui.select({options = labels, selectedIndex = index, onChange = callback,
		enabled = status.phase ~= "pending" and status.phase ~= "applying", placeholder = noctalia.tr("panel.no_monitor")})
end

render = function()
	local busy = status.phase == "pending" or status.phase == "applying"
	local enabled = status.available == true and not busy and source ~= nil
	local children = {
		ui.row({justify = "space_between", align = "center"}, {
			ui.label({text = noctalia.tr("projection.title"), fontSize = 18, fontWeight = "bold"}),
			ui.button({glyph = "close", variant = "ghost", onClick = "onCloseClicked", visible = not embedded}),
		}),
		ui.label({text = noctalia.tr("projection.source"), color = "on_surface_variant"}),
		selector(source, "onSourceChanged", nil),
		ui.label({text = noctalia.tr("projection.destination"), color = "on_surface_variant"}),
		selector(destination, "onDestinationChanged", source),
	}
	local modeButtons = {}
	for _, item in ipairs({
		{"laptop", "device-laptop", "onLaptop"},
		{"duplicate", "screen-share", "onDuplicate"},
		{"extend", "devices", "onExtend"},
		{"external", "device-desktop", "onExternal"},
	}) do
		local current = item[1] == "laptop" and laptopOnlyActive()
		table.insert(modeButtons, ui.button({text = noctalia.tr(current and "projection.laptop_current" or "projection." .. item[1]), glyph = item[2],
			onClick = item[3], controlSize = "lg", variant = "secondary", flexGrow = embedded and 1 or 0,
			enabled = enabled and not current and (item[1] == "laptop" or destination ~= nil)}))
	end
	if embedded then
		table.insert(children, ui.row({gap = 8}, {modeButtons[1], modeButtons[2]}))
		table.insert(children, ui.row({gap = 8}, {modeButtons[3], modeButtons[4]}))
	else
		for _, button in ipairs(modeButtons) do table.insert(children, button) end
	end
	local message = noctalia.tr("projection.hint")
	if not status.available then message = noctalia.tr("projection.missing")
	elseif status.phase == "error" then message = status.message or noctalia.tr("projection.query_failed")
	elseif busy then message = noctalia.tr("projection." .. status.phase, {seconds = status.seconds or 15})
	elseif status.phase == "unchanged" then message = noctalia.tr("projection.unchanged")
	elseif status.phase == "reverted" or status.phase == "kept" then message = noctalia.tr("projection." .. status.phase)
	elseif destination == nil then message = noctalia.tr("projection.connect_display") end
	local mirror = noctalia.state.get("mirror_status") or {}
	local mirrorError = status.phase == "kept" and status.mode == "duplicate" and mirror.phase == "error"
	if mirrorError then message = mirror.message or noctalia.tr("projection.query_failed") end
	table.insert(children, ui.label({text = message, maxLines = 3, color = (status.phase == "error" or mirrorError) and "error" or "on_surface_variant"}))
	if status.phase == "pending" then
		table.insert(children, ui.row({gap = 8}, {
			ui.button({text = noctalia.tr("projection.keep"), variant = "primary", onClick = "onKeep"}),
			ui.button({text = noctalia.tr("projection.revert"), onClick = "onRevert"}),
		}))
	end
	panel.render(ui.column({gap = 10}, children))
end

noctalia.state.watch("projection_status", function(value)
	if type(value) == "table" then status = value; if opened then render() end end
end)
noctalia.state.watch("projection_outputs", function(value)
	selectOutputs(value)
	if opened then render() end
end)
noctalia.state.watch("mirror_status", function()
	if opened then render() end
end)

function onOpen(context)
	opened = true
	embedded = context == "embedded"
	status = noctalia.state.get("projection_status") or status
	selectOutputs(noctalia.state.get("projection_outputs"))
	render()
	request("refresh")
end
function onClose() opened = false end
function onCloseClicked() panel.close() end
function onKeep() request("keep") end
function onRevert() request("revert") end
function onLaptop() if not laptopOnlyActive() then request("apply", "laptop") end end
function onDuplicate() request("apply", "duplicate") end
function onExtend() request("apply", "extend") end
function onExternal() request("apply", "external") end
function onSourceChanged(index)
	local output = outputs[(tonumber(index) or -1) + 1]
	if output == nil then return end
	source = output.name
	selectOutputs(outputs)
	render()
end
function onDestinationChanged(index)
	local count = 0
	for _, output in ipairs(outputs) do
		if output.name ~= source then
			if count == tonumber(index) then destination = output.name; render(); return end
			count += 1
		end
	end
end

--!nonstrict

local directory = noctalia.pluginDir()
local data = noctalia.pluginDataDir()
local control = data and (data .. "/projection.control") or nil
local available = directory ~= nil and control ~= nil
	and noctalia.commandExists("python3") and noctalia.commandExists("wlr-randr")
local busy = false
local querying = false
local queued = nil

local function quote(value)
	return "'" .. string.gsub(tostring(value), "'", "'\\''") .. "'"
end

local function publish(value)
	value.available = available
	noctalia.state.set("projection_status", value)
end

local function refresh()
	if not available or querying then return end
	querying = true
	local accepted = noctalia.runAsync("python3 " .. quote(directory .. "/projection.py") .. " list", function(result)
		querying = false
		local value = noctalia.json.decode(result.stdout or "")
		if result.exitCode == 0 and type(value) == "table" then
			noctalia.state.set("projection_outputs", value)
		elseif not busy then
			publish({phase = "error", message = noctalia.tr("projection.query_failed")})
		end
	end, 10000)
	if not accepted then
		querying = false
		publish({phase = "error", message = noctalia.tr("projection.launch_failed")})
	end
end

local function launch(request)
	queued = nil
	if noctalia.writeFile(control, "pending") ~= true then
		busy = false
		publish({phase = "error", message = noctalia.tr("service.data_unavailable")})
		return
	end
	local command = "python3 " .. quote(directory .. "/projection.py") .. " apply "
		.. quote(request.mode) .. " " .. quote(request.source) .. " "
		.. quote(request.destination or "") .. " " .. quote(control)
		.. [[; printf '\n{"phase":"exited"}\n']]
	local accepted = noctalia.runStream(command, function(line)
		local value = noctalia.json.decode(line)
		if type(value) ~= "table" then return end
		if value.phase == "exited" then
			if not busy then return end
			value = {phase = "error", message = noctalia.tr("projection.launch_failed")}
		end
		if value.phase == "kept" or value.phase == "reverted" or value.phase == "unchanged" or value.phase == "error" then
			busy = false
			noctalia.removeFile(control)
			refresh()
		end
		publish(value)
		-- Duplicate uses the existing managed mirror after the user confirms
		-- the output layout. Failed capture remains visible in mirror_status.
		if value.phase == "kept" and value.mode == "duplicate" then
			noctalia.state.set("mirror_request", {
				action = "start", source = value.source, destination = value.destination,
			})
		end
	end)
	if not accepted then
		busy = false
		noctalia.removeFile(control)
		publish({phase = "error", message = noctalia.tr("projection.launch_failed")})
	end
end

noctalia.state.watch("mirror_status", function(value)
	if queued == nil or type(value) ~= "table" then return end
	if value.phase == "stopped" then
		launch(queued)
	elseif value.phase == "error" then
		queued = nil
		busy = false
		publish({phase = "error", message = value.message})
	end
end)

noctalia.state.watch("projection_request", function(request)
	if type(request) ~= "table" then return end
	if request.action == "refresh" then
		refresh()
	elseif request.action == "keep" or request.action == "revert" then
		if busy and queued == nil then noctalia.writeFile(control, request.action) end
	elseif request.action == "apply" and available and not busy then
		local mode = request.mode
		if mode ~= "laptop" and mode ~= "duplicate" and mode ~= "extend" and mode ~= "external" then return end
		if type(request.source) ~= "string" then return end
		busy = true
		publish({phase = "applying", mode = mode})
		local mirror = noctalia.state.get("mirror_status") or {}
		if mirror.phase == "starting" or mirror.phase == "running" or mirror.phase == "stopping" then
			queued = request
			noctalia.state.set("mirror_request", {action = "stop"})
		else
			launch(request)
		end
	end
end)

function onOutputsChanged()
	refresh()
end

function onExit()
	if control ~= nil then noctalia.removeFile(control) end
end

publish({phase = "idle"})
refresh()

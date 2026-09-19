--!nonstrict

local model = require("./display-model.luau")
local directory, data = noctalia.pluginDir(), noctalia.pluginDataDir()
local available = directory ~= nil and data ~= nil and noctalia.commandExists("python3") and noctalia.commandExists("wlr-randr")
local busy, querying, refreshQueued, querySerial, generation = false, false, false, 0, 0
local queued, owned, priorMirror, restoreAfter = nil, nil, nil, nil
local refresh, launch

local function quote(value) return "'" .. string.gsub(tostring(value), "'", "'\\''") .. "'" end
local function publish(value)
	if value.available == nil then value.available = available end
	noctalia.state.set("projection_status", value)
end
local function cleanup(paths)
	if paths then noctalia.removeFile(paths.control); noctalia.removeFile(paths.request) end
end
local function invalidateQuery()
	querySerial += 1
	querying, refreshQueued = false, false
end
local function acceptOutputs(value)
	local numbered = noctalia.state.get("projection_numbers") or { values = {}, next = 1 }
	local numbers, nextNumber = model.number(value, numbered.values, numbered.next)
	noctalia.state.set("projection_numbers", { values = numbers, next = nextNumber })
	noctalia.state.set("projection_outputs", value)
end
local function resumeMirror()
	local pair = restoreAfter
	if not pair then return end
	restoreAfter = nil
	noctalia.state.set("projection_recovery", nil)
	local heads = noctalia.state.get("projection_outputs") or {}
	local source, destination = model.find(heads, pair.source), model.find(heads, pair.destination)
	if source and destination and source.enabled and destination.enabled then
		noctalia.state.set("mirror_request", { action = "start", source = pair.source, destination = pair.destination })
	else publish({ phase = "error", message = noctalia.tr("projection.mirror_restore_failed") }) end
end

refresh = function(completed)
	if not available then return end
	if querying then refreshQueued = true; return end
	querying, refreshQueued, querySerial = true, false, querySerial + 1
	local serial = querySerial
	local function resultReady(result)
		if serial ~= querySerial then return end
		querying = false
		local value = noctalia.json.decode(result.stdout or "")
		local ok = result.exitCode == 0 and type(value) == "table"
		if ok then acceptOutputs(value) end
		if completed then completed(ok) end
		if not ok and not busy then publish({ phase = "error", message = noctalia.tr("projection.query_failed") }) end
		if ok and not busy then resumeMirror() end
		if refreshQueued and not busy then refresh() end
	end
	if not noctalia.runAsync("python3 " .. quote(directory .. "/projection.py") .. " list", resultReady, 10000) then
		resultReady({ exitCode = 1 })
	end
end

local function finish(value, transactionGeneration)
	if transactionGeneration ~= generation then return end
	queued = nil
	cleanup(owned)
	owned = nil
	if value.phase == "reverted" or value.phase == "error" then restoreAfter = priorMirror
	elseif value.mode == "duplicate" then restoreAfter = { source = value.source, destination = value.destination }
	else restoreAfter = nil end
	priorMirror = nil
	-- Discard any pre-transaction query. Its late reply must not overwrite the
	-- confirmed topology or start a mirror against outputs not yet restored.
	invalidateQuery()
	refresh(function(ok)
		busy = false
		noctalia.state.set("projection_recovery", not ok and restoreAfter and { priorMirror = restoreAfter } or nil)
		publish(ok and value or { phase = "error", message = noctalia.tr("projection.query_failed") })
	end)
end

launch = function(request)
	queued = nil
	local paths, transactionGeneration = owned, generation
	local function fail(message) finish({ phase = "error", message = message }, transactionGeneration) end
	if noctalia.writeFile(paths.control, "pending") ~= true then fail(noctalia.tr("service.data_unavailable")); return end
	local command
	if request.action == "apply_layout" then
		local encoded = noctalia.json.encode(request.layout)
		if type(encoded) ~= "string" or noctalia.writeFile(paths.request, encoded) ~= true then fail(noctalia.tr("projection.invalid_request")); return end
		command = "python3 " .. quote(directory .. "/projection.py") .. " apply-layout " .. quote(paths.request) .. " " .. quote(paths.control)
	else
		command = "python3 " .. quote(directory .. "/projection.py") .. " apply " .. quote(request.mode) .. " " .. quote(request.source or "") .. " " .. quote(request.destination or "") .. " " .. quote(paths.control)
	end
	local done, confirmationShown = false, false
	local accepted = noctalia.runStream(command .. [[; printf '\n{"phase":"exited"}\n']], function(line)
		if transactionGeneration ~= generation or done then return end
		local value = noctalia.json.decode(line)
		if type(value) ~= "table" then return end
		if value.phase == "exited" then done = true; fail(noctalia.tr("projection.launch_failed")); return end
		if value.phase == "kept" or value.phase == "reverted" or value.phase == "unchanged" or value.phase == "error" then
			done = true; finish(value, transactionGeneration)
		elseif value.phase == "pending" then
			publish(value)
			if not confirmationShown then
				confirmationShown = true
				if noctalia.state.get("projection_panel_open") ~= true then
					noctalia.togglePanel("elijaharch/wl-screen-mirror:projection")
				end
			end
		end
	end)
	if not accepted and not done then done = true; fail(noctalia.tr("projection.launch_failed")) end
end

local function validRequest(request)
	if request.action == "apply_layout" then return type(request.layout) == "table" end
	local heads = noctalia.state.get("projection_outputs") or {}
	local source, destination = model.find(heads, request.source), model.find(heads, request.destination)
	if request.mode == "laptop" then return source ~= nil and model.isInternal(source.name) end
	if request.mode == "single" then return source ~= nil end
	if request.mode == "duplicate" then return source ~= nil and destination ~= nil and source.name ~= destination.name end
	if request.mode == "external" then
		for _, head in ipairs(heads) do if not model.isInternal(head.name) then return true end end
		return false
	end
	return request.mode == "extend" and #heads > 0
end

noctalia.state.watch("mirror_status", function(value)
	if queued == nil or type(value) ~= "table" then return end
	if value.phase == "stopped" then launch(queued)
	elseif value.phase == "error" then finish({ phase = "error", message = value.message or noctalia.tr("projection.launch_failed") }, generation) end
end)

noctalia.state.watch("projection_request", function(request)
	if type(request) ~= "table" then return end
	if request.action == "refresh" then if busy then refreshQueued = true else refresh() end; return end
	if request.action == "keep" or request.action == "revert" then
		local status = noctalia.state.get("projection_status") or {}
		if busy and queued == nil and owned and status.phase == "pending" then noctalia.writeFile(owned.control, request.action) end
		return
	end
	if (request.action ~= "apply" and request.action ~= "apply_layout") or not available or busy then return end
	if not validRequest(request) then publish({ phase = "error", message = noctalia.tr("projection.invalid_request") }); return end
	busy, generation = true, generation + 1
	invalidateQuery()
	publish({ phase = "applying", mode = request.action == "apply_layout" and "layout" or request.mode })
	local mirror = noctalia.state.get("mirror_status") or {}
	priorMirror = (mirror.phase == "running" or mirror.phase == "starting") and { source = mirror.source, destination = mirror.destination } or nil
	local sequence = (tonumber(noctalia.state.get("projection_sequence")) or 0) + 1
	noctalia.state.set("projection_sequence", sequence)
	local id = tostring(noctalia.nowMs()) .. "-" .. tostring(sequence)
	owned = { control = data .. "/projection-control-" .. id, request = data .. "/projection-request-" .. id }
	noctalia.state.set("projection_recovery", { priorMirror = priorMirror, ownedpaths = owned })
	if priorMirror or mirror.phase == "stopping" then
		queued = request
		if mirror.phase ~= "stopping" then noctalia.state.set("mirror_request", { action = "stop" }) end
	else launch(request) end
end)

local function settleRecovery(recovery)
	busy = true
	publish({ phase = "applying" })
	cleanup(recovery.ownedpaths)
	local accepted = noctalia.runAsync("python3 " .. quote(directory .. "/projection.py") .. " settle " .. quote(data), function(result)
		local value = noctalia.json.decode(result.stdout or "")
		if result.exitCode == 0 and type(value) == "table" then
			acceptOutputs(value)
			busy = false
			noctalia.state.set("projection_recovery", nil)
			restoreAfter = recovery.priorMirror
			publish({ phase = "reverted" })
			resumeMirror()
		else
			-- Keep the recovery marker and block new transactions rather than race
			-- an old helper whose rollback has not been observed to finish.
			publish({ phase = "error", message = noctalia.tr("projection.recovery_failed"), available = false })
		end
	end, 30000)
	if not accepted then publish({ phase = "error", message = noctalia.tr("projection.recovery_failed"), available = false }) end
end

function onOutputsChanged() if busy then refreshQueued = true else refresh() end end
function onExit()
	generation += 1
	invalidateQuery()
	cleanup(owned)
	owned = nil
end

publish({ phase = "idle" })
local recovery = noctalia.state.get("projection_recovery")
if available and type(recovery) == "table" then settleRecovery(recovery) else refresh() end

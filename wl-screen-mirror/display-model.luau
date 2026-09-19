--!nonstrict
-- Pure display-arrangement state helpers shared by the projection entries.
local M = {}

local internalPrefixes = { "eDP-", "LVDS-", "DSI-" }
local coordinateLimit = 1000000

function M.isInternal(name)
	for _, prefix in ipairs(internalPrefixes) do
		if string.sub(name or "", 1, #prefix) == prefix then return true end
	end
	return false
end

function M.copy(value)
	if type(value) ~= "table" then return value end
	local result = {}
	for key, item in pairs(value) do result[key] = M.copy(item) end
	return result
end

local function currentMode(output)
	for _, candidate in ipairs(output.modes or {}) do if candidate.current then return candidate end end
end

function M.fingerprint(outputs)
	local parts = {}
	for _, output in ipairs(outputs or {}) do
		-- Disabled geometry and descriptive annotations are not topology.
		if output.enabled then
			local position, mode = output.position or {}, currentMode(output) or {}
			table.insert(parts, table.concat({ output.name or "", "true", tostring(position.x or ""), tostring(position.y or ""),
				tostring(mode.width or ""), tostring(mode.height or ""), tostring(mode.refresh or ""), tostring(output.scale or ""),
				tostring(output.transform or "normal"), tostring(output.adaptive_sync == true) }, "\0"))
		else table.insert(parts, table.concat({ output.name or "", "false" }, "\0")) end
	end
	table.sort(parts)
	return table.concat(parts, "\1")
end

function M.number(outputs, numbers, nextNumber)
	numbers, nextNumber = numbers or {}, nextNumber or 1
	-- Allocate built-ins first so the initial internal connector remains 1 even
	-- when the compositor reports an external head first.
	for pass = 1, 2 do for _, output in ipairs(outputs or {}) do
		if (pass == 1) == M.isInternal(output.name) and numbers[output.name] == nil then
			numbers[output.name] = nextNumber
			nextNumber += 1
		end
	end end
	return numbers, nextNumber
end

function M.enabled(outputs)
	local result = {}
	for _, output in ipairs(outputs or {}) do if output.enabled then table.insert(result, output) end end
	return result
end

function M.find(outputs, name)
	for _, output in ipairs(outputs or {}) do if output.name == name then return output end end
end

function M.rectanglesOverlap(left, right)
	return left.x < right.x + right.width and right.x < left.x + left.width
		and left.y < right.y + right.height and right.y < left.y + left.height
end

local function coordinate(value) return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge and math.abs(value) <= coordinateLimit end

function M.validDraft(draft)
	local active = M.enabled(draft)
	if #active == 0 then return false, "projection.last_display" end
	for index, left in ipairs(active) do
		if not coordinate(left.x) or not coordinate(left.y) then return false, "projection.invalid_position" end
		if not coordinate(left.width) or not coordinate(left.height) or left.width <= 0 or left.height <= 0 then return false, "projection.invalid_position" end
		for otherIndex, right in ipairs(active) do
			if index < otherIndex and M.rectanglesOverlap(left, right) then return false, "projection.overlap" end
		end
	end
	return true
end

function M.snap(draft, id, x, y)
	local item = M.find(draft, id)
	if item == nil or not item.enabled or not coordinate(x) or not coordinate(y) then return nil, "projection.invalid_position" end
	local result = M.copy(draft)
	item = M.find(result, id)
	item.x, item.y = math.floor(x), math.floor(y)
	local threshold, bestX, bestY, distanceX, distanceY = 24, item.x, item.y, 25, 25
	-- Select one nearest original edge per axis; never chain a prior snap into another.
	for _, other in ipairs(result) do
		if other.name ~= id and other.enabled then
			for _, candidate in ipairs({ other.x, other.x + other.width, other.x - item.width }) do
				local distance = math.abs(item.x - candidate)
				if distance <= threshold and distance < distanceX then bestX, distanceX = candidate, distance end
			end
			for _, candidate in ipairs({ other.y, other.y + other.height, other.y - item.height }) do
				local distance = math.abs(item.y - candidate)
				if distance <= threshold and distance < distanceY then bestY, distanceY = candidate, distance end
			end
		end
	end
	item.x, item.y = bestX, bestY
	local ok, reason = M.validDraft(result)
	return ok and result or nil, reason
end

function M.sameLayout(left, right)
	if #left ~= #right then return false end
	for _, item in ipairs(left) do
		local other = M.find(right, item.name)
		if not other or item.enabled ~= other.enabled or (item.enabled and (item.x ~= other.x or item.y ~= other.y)) then return false end
	end
	return true
end

function M.draft(outputs)
	local result = {}
	for _, output in ipairs(outputs or {}) do
		local position, current = output.position or {}, currentMode(output)
		if not current then for _, mode in ipairs(output.modes or {}) do if mode.preferred then current = mode; break end end end
		local mode = current and (tostring(current.width) .. "×" .. tostring(current.height) .. " @ " .. tostring(current.refresh) .. " Hz") or ""
		local width = output.logical_width or (current and current.width) or 1
		local height = output.logical_height or (current and current.height) or 1
		table.insert(result, { name = output.name, enabled = output.enabled == true, x = position.x or 0, y = position.y or 0,
			width = width, height = height, scale = output.scale or 1, mode = mode })
	end
	return result
end

function M.request(baseline, draft)
	local outputs = {}
	for _, output in ipairs(draft or {}) do table.insert(outputs, { name = output.name, enabled = output.enabled, x = output.x, y = output.y }) end
	return { baseline = M.copy(baseline), outputs = outputs }
end

return M

--!nonstrict

local PANEL_ID = "elijaharch/wl-screen-mirror:projection"

local function render(status)
	local phase = type(status) == "table" and status.phase or "stopped"
	shortcut.setLabel(noctalia.tr("projection.title"))
	shortcut.setIcon("screen-share")
	shortcut.setActive(phase == "running")
	-- Keep the panel reachable to explain missing dependencies or displays.
	shortcut.setEnabled(true)
end

noctalia.state.watch("mirror_status", render)
render(noctalia.state.get("mirror_status"))

function onClick()
	noctalia.togglePanel(PANEL_ID)
end

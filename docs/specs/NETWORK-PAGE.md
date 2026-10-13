# NETWORK-PAGE - layout, keys and render contracts for page 3 (maintainer + renderer tasks)

Reference terminal 120 x 35. Page 3 gets 33 rows (35 - alert banner - status bar).
```
+- Wi-Fi networks (3fr, h=15) ----------------------------+ +- Saved networks & VPN (2fr) ---------+
| content 68 x 13                                         | | content 44 x 13                      |
+---------------------------------------------------------+ +--------------------------------------+
+- Tools (3fr, 1fr = 18 rows) ----------------------------+ +- Your Wi-Fi link (2fr, h=10) --------+
| menu column 22 wide | result view (w - 24)              | | content 44 x 8                       |
| content 68 x 16                                         | +- Events (1fr = 8) -------------------+
|                                                         | | content 44 x 6                       |
+---------------------------------------------------------+ +--------------------------------------+
```
Smallest supported 100 x 30: renderers must still produce sensible output at Wi-Fi w=56 h=10, saved w=36, tools w=56 h=13,
link w=36 h=6, events w=36 h=4. Narrower than that: drop columns, never wrap.

## Focus and keys (page 3)
Three boxes take focus: `wifi`, `saved`, `tools`. Left/Right (or h/l) move focus. Up/Down (j/k), PgUp/PgDn, Home/End move the
cursor inside the focused box. The focused box's renderer gets `focused=True` and draws the cursor row on `#1f3550`
(unfocused cursor row: `#1a1f27`). Cursor rows are padded to the full width so the highlight is a bar.
| Key | Action |
|---|---|
| Enter | wifi: action menu for the network (Connect / Disconnect / Forget / Details); saved: action menu for the profile; tools: run the selected tool |
| r | rescan Wi-Fi now |
| t | open the Target dialog (address + ports) and run Trace & ports |
| d | run the Network doctor |
| w | Wi-Fi on/off (confirm) |
| Esc | cancel the running tool, else close the result view |
All changes go through `ctop/netctl.py` and are confirmed in a menu first (T30 screens).

## Render functions (who builds what)
* T29a `ctop/render/net_wifi.py`: `wifi_list_lines`, `saved_lines`, `link_lines`, `events_lines`.
* T29b `ctop/render/net_tools.py`: `menu_lines`, `tool_view_lines` (+ one function per tool view).
* The maintainer's panels (`ctop/panels/network_page.py`) glue them: tools box = `hcat([menu_lines(...), tool_view_lines(...)], [22, w - 24])`.

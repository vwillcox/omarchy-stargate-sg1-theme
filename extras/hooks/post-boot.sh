#!/bin/bash
# Restart the stargate-sg1 gate-room wallpaper after login. The theme-set hook
# that normally starts it only runs on `omarchy theme set`, so nothing brings
# mpvpaper back after a reboot.
#
# Wait for omarchy-shell's static background to map first: mpvpaper shares
# Hyprland's background layer with it, and the surface mapped last draws on
# top, so starting the video earlier would leave it buried.
THEME_NAME=$(cat "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/current/theme.name" 2>/dev/null)

[[ $THEME_NAME == "stargate-sg1" ]] || exit 0

for _ in {1..60}; do
  hyprctl layers -j 2>/dev/null | jq -e '
    any(.[]; (.levels["0"] // []) | any(.namespace == "omarchy-background"))' >/dev/null && break
  sleep 0.5
done

omarchy-sgc-gate-toggle on

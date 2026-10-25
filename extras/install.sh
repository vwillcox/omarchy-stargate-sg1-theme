#!/bin/bash
# Install the parts of the stargate-sg1 theme that live outside the theme
# directory: the SGC bar plugin, the theme-set hook, the gate wallpaper toggle
# (+ its stacking guard), the after-login restart and the Michroma / Share Tech
# Mono fonts. Safe to re-run (after an Omarchy update, re-run it to rebuild the
# bar from the new stock bar).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME="$(dirname "$HERE")"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
HOOKS="$CFG/omarchy/hooks"
STOCK_BAR="${OMARCHY_PATH:-/usr/share/omarchy}/shell/plugins/bar"
SGC_BAR="vwillcox.sgc-bar"

missing=()
for cmd in mpvpaper pw-play jq socat python3; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done

mkdir -p "$CFG/omarchy/plugins/$SGC_BAR" "$HOOKS/theme-set.d" "$HOOKS/post-boot.d" \
  "$HOME/.local/bin" "$HOME/.local/share/fonts/sgc"

# The SGC bar is the stock bar with the gate console patched in. Patch this
# system's own stock bar so it matches the installed Omarchy; the patcher
# checks every edit before writing, and if the stock bar has changed too much,
# fall back to the copy built when the theme was released.
build=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/sgc-bar.XXXXXX")
if [[ -d $STOCK_BAR ]] && cp -a "$STOCK_BAR/." "$build/" &&
  python3 -B "$THEME/tools/patch_sgc_bar.py" "$build" &&
  cp "$HERE/plugins/$SGC_BAR/GateTimeline.js" "$build/"; then
  cp -a "$build/." "$CFG/omarchy/plugins/$SGC_BAR/"
  echo "SGC bar: built from this system's stock bar."
else
  cp -a "$HERE/plugins/$SGC_BAR/." "$CFG/omarchy/plugins/$SGC_BAR/"
  echo "SGC bar: the stock bar has changed since this theme was made; installed the bundled copy instead." >&2
fi

install -m 755 "$HERE/hooks/stargate-sg1.sh" "$HOOKS/theme-set.d/stargate-sg1.sh"
for bin in "$HERE"/bin/*; do
  install -m 755 "$bin" "$HOME/.local/bin/$(basename "$bin")"
done
cp -a "$HERE"/fonts/*.ttf "$HOME/.local/share/fonts/sgc/"
fc-cache -f "$HOME/.local/share/fonts" >/dev/null

# After-login restart of the video wallpaper, unless a post-boot hook of the
# user's own already starts it.
if ! grep -qs 'omarchy-sgc-gate-toggle' "$HOOKS"/post-boot.d/*; then
  install -m 755 "$HERE/hooks/post-boot.sh" "$HOOKS/post-boot.d/stargate-sg1.sh"
fi

omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true

if ((${#missing[@]})); then
  echo
  echo "Missing: ${missing[*]}. The gate wallpaper needs mpvpaper (omarchy pkg aur add mpvpaper);"
  echo "sounds need pw-play (PipeWire); the bar sync and the guard need jq and socat."
fi

echo
if [[ $(cat "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/current/theme.name" 2>/dev/null) == "stargate-sg1" ]]; then
  echo "Re-applying stargate-sg1 to bring up the gate and the SGC bar..."
  omarchy theme set stargate-sg1
else
  echo "Installed. Apply the theme with: omarchy theme set stargate-sg1"
fi

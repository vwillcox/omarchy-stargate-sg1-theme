#!/bin/bash
# stargate-sg1 theme integration:
#  - swaps the status bar to the SGC bar plugin, and back to whatever bar was
#    in use before when another theme is set
#  - starts/stops the animated gate-room wallpaper
#  - dials the gate once (sound) as the theme comes up
THEME_NAME="${1:-}"
SHELL_JSON="$HOME/.config/omarchy/shell.json"
SGC_BAR="vwillcox.sgc-bar"
SGC_BAR_DIR="$HOME/.config/omarchy/plugins/$SGC_BAR"
PREVIOUS_BAR_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/stargate-sg1.previous-bar"
DIAL="$HOME/.config/omarchy/themes/stargate-sg1/sounds/dial.wav"

edit_shell_json() {
  local filter="$1" current new
  shift
  [[ -f $SHELL_JSON ]] || return 0
  current=$(<"$SHELL_JSON")
  new=$(jq --arg bar "$SGC_BAR" "$@" "$filter" <<<"$current") || return 0
  [[ -n $new && $new != "$current" ]] || return 0
  printf '%s\n' "$new" >"$SHELL_JSON"
}

# The shell hot-swaps the bar when bar.id changes, but a plugin bar loaded that
# way comes up without its registry widgets (menu, tray, network, audio...);
# only a fresh shell start builds it fully. Restart once theme-set (which still
# talks to the shell after the hooks) has finished. The video wallpaper guard
# re-stacks mpvpaper above the shell's re-mapped static background.
restart_shell_after_theme_set() {
  setsid -f bash -c '
    for _ in {1..300}; do pgrep -f "^(/usr)?/bin/bash [^ ]*/bin/omarchy-theme-set " >/dev/null || break; sleep 0.1; done
    omarchy-restart-shell' >/dev/null 2>&1 </dev/null
}

current_bar() {
  jq -r '.bar.id // ""' "$SHELL_JSON" 2>/dev/null
}

if [[ $THEME_NAME == "stargate-sg1" ]]; then
  # No plugin (extras/install.sh not run, or it failed): keep the user's bar.
  if [[ -f $SGC_BAR_DIR/manifest.json ]]; then
    previous_bar=$(current_bar)
    if [[ $previous_bar != "$SGC_BAR" ]]; then
      mkdir -p "${PREVIOUS_BAR_FILE%/*}"
      printf '%s\n' "$previous_bar" >"$PREVIOUS_BAR_FILE"
      edit_shell_json '.bar.id = $bar'
      restart_shell_after_theme_set
    fi
  fi
  omarchy-sgc-gate-toggle on
  if [[ -f $DIAL ]] && command -v pw-play >/dev/null 2>&1; then
    nohup pw-play "$DIAL" >/dev/null 2>&1 &
    disown
  fi
else
  # Only hand the bar back if it is still ours: another theme's hook may
  # already have put its own bar in.
  if [[ $(current_bar) == "$SGC_BAR" ]]; then
    previous_bar=$(cat "$PREVIOUS_BAR_FILE" 2>/dev/null)
    edit_shell_json 'if $prev == "" then del(.bar.id) else .bar.id = $prev end' --arg prev "$previous_bar"
    # A plugin bar needs a fresh shell start to come up complete (see above);
    # the stock bar hot-swaps cleanly.
    [[ -n $previous_bar ]] && restart_shell_after_theme_set
  fi
  omarchy-sgc-gate-toggle off
fi

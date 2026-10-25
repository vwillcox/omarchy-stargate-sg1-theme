# Stargate SG-1 — an Omarchy theme

![Stargate Command gate room](preview.png)

Stargate Command for [Omarchy](https://omarchy.org): you are standing on the
gate-room floor at the foot of the ramp, and the gate keeps dialing out.

- **Gate-room wallpaper** (`gate-loop.mp4`, a seamless 53 s loop played with
  mpvpaper). The glyph ring spins back and forth, and the top chevron engages
  on each of seven symbols, lighting the chevrons in SG-1 order (1–3 down the
  right, 4–6 up the left, 7 at the top). The kawoosh blows out of the ring and
  falls back into a rippling event horizon that throws caustics over the room.
  The iris spirals shut over the live wormhole, holds, and reopens. The
  wormhole then disengages and the chevrons go dark. Rendered in Blender
  Cycles from a procedural scene (`tools/`). A still frame is in
  `backgrounds/`.
- **SGC bar** (a skinned clone of Omarchy's own bar). It looks like a
  dialing-computer console: cut-corner panels framed in event-horizon blue,
  with amber labels (UPLINK, ASGARD, NAQUADAH, SURFACE…) and Michroma / Share
  Tech Mono lettering.
  - Workspaces are gate chevrons. The focused one glows and engages when it
    locks, and clicking one plays a DHD press.
  - The left cap holds a miniature stargate. Next to the clock is a
    gate-status readout ("ENCODING CHEVRON FOUR", "CHEVRON SEVEN LOCKED",
    "WORMHOLE ESTABLISHED", a blinking red "CLOSING THE IRIS", …) with seven
    chevron pips.
  - Both stay in step with the wallpaper: the bar reads the video's playback
    position from mpv and plays the same timeline the loop was rendered from.
    With the video off, the gate rests.
- **Shell styling**: blue-black console panels with a blue→amber gradient
  frame and chevron-amber selection, across popups, menus, launcher,
  notifications and the lock screen. Window borders go from event-horizon
  blue to chevron orange.
- **Sounds** (synthesized from sines and filtered noise, nothing sampled from
  the show): the gate dials once when the theme is applied, and workspace
  clicks are DHD presses.

## Install

```bash
omarchy theme install https://github.com/vwillcox/omarchy-stargate-sg1-theme
```

That gives you the colours, the shell styling and the still wallpaper.
Omarchy deliberately doesn't let a theme run code, so the animated gate and
the SGC bar come from an install script you run yourself (please read it
first, it's short):

```bash
omarchy pkg aur add mpvpaper      # plays the video wallpaper
~/.config/omarchy/themes/stargate-sg1/extras/install.sh
```

The script installs:

| What | Where |
|------|-------|
| SGC bar plugin, built from your own Omarchy's stock bar | `~/.config/omarchy/plugins/vwillcox.sgc-bar/` |
| Theme-set hook: swaps the bar in and out, starts and stops the video | `~/.config/omarchy/hooks/theme-set.d/stargate-sg1.sh` |
| Post-boot hook: brings the video back after login | `~/.config/omarchy/hooks/post-boot.d/stargate-sg1.sh` |
| `omarchy-sgc-gate-toggle` and `omarchy-video-wallpaper-guard` | `~/.local/bin/` |
| Michroma and Share Tech Mono fonts | `~/.local/share/fonts/sgc/` |

If the theme is already active, it re-applies it so the gate and the bar
come up straight away.

Good to know:

- **Your bar comes back.** Switching to another theme restores whichever bar
  you had before, stock or your own plugin.
- **After an Omarchy update**, re-run `extras/install.sh` to rebuild the bar
  from the updated stock bar. If the stock bar has changed too much for the
  patch to apply, the script installs the bundled copy and tells you.
- **The video is 3440×1440** (21:9) at 30 fps. On 16:9 screens it fills the
  screen by trimming the sides; the gate stays centred. mpv decodes it on
  the GPU where it can.
- `omarchy-sgc-gate-toggle [on|off|toggle|restart]` controls the video by
  hand. The guard it starts restarts the video when a monitor hotplug or a
  shell restart leaves the static background stacked on top of it.

Requirements: Omarchy with the Quickshell-based shell, `mpvpaper`, and
`pw-play` (PipeWire) for the sounds. `jq` and `socat` ship with Omarchy.

## Uninstall

Switch to another theme first (that hands the bar back and stops the video),
then delete the files in the table above and
`omarchy theme remove stargate-sg1`.

## Rebuilding

Everything is reproducible from `tools/`. You need Blender 5.x (Cycles; an
RTX card renders a frame in about 10 s), Python with `numpy` and `OpenEXR`,
and `ffmpeg`.

```bash
python3 tools/sgc_timeline.py          # print the schedule (all timings live here)

# frames: ~1200 unique of 1601 at 3440x1440
blender -b --factory-startup -P tools/sgc_gate.py -- OUT --skip-existing
python3 tools/gate_compose.py OUT gate-loop.mp4
python3 tools/gate_compose.py OUT --still backgrounds/1-gate.png --still-frame 960

# bar: re-skin a fresh copy of the stock bar, regenerate the timeline
cp -r /usr/share/omarchy/shell/plugins/bar /tmp/sgc-bar
python3 tools/patch_sgc_bar.py /tmp/sgc-bar
python3 tools/gen_gate_js.py /tmp/sgc-bar/GateTimeline.js

python3 tools/sgc_sounds.py sounds
```

Faster checks: `--scale 25 --samples 24` renders the whole loop in about
7 minutes, `--preview` renders only the motion in Workbench, and
`--look 28.3,40` renders single moments. If you change the timeline,
re-render and regenerate `GateTimeline.js` together so the bar stays in step.

If you fork this, keep the repository name ending in `stargate-sg1-theme`:
Omarchy names the theme after the repo, and the scripts look for
`~/.config/omarchy/themes/stargate-sg1`.

## License and credits

The theme (scripts, bar skin, wallpaper video and stills, sounds) is MIT
licensed; see `LICENSE`.

Fonts: [Michroma](https://fonts.google.com/specimen/Michroma) and
[Share Tech Mono](https://fonts.google.com/specimen/Share+Tech+Mono), both
under the SIL Open Font License (`extras/fonts/`).

Stargate SG-1 is a trademark of MGM. This is a non-commercial fan theme, not
affiliated with or endorsed by MGM. The gate, its glyphs, the room and the
sounds were all made from scratch for this theme; nothing is taken from the
show.

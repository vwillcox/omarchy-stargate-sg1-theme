#!/usr/bin/env python3
"""Write GateTimeline.js for the vwillcox.sgc-bar plugin from sgc_timeline.py,
so the bar's gate readout and mini gate follow the same schedule as the
rendered wallpaper loop.

Usage: gen_gate_js.py OUT.js
"""
import json
import sys

import sgc_timeline as TL

CONSTS = {
    "FPS": TL.FPS,
    "GLYPHS": TL.GLYPHS,
    "DURATION": TL.DURATION,
    "LOCK_DOWN": TL.LOCK_DOWN,
    "SHUTDOWN": TL.SHUTDOWN,
    "CHEVRONS_OFF": TL.CHEVRONS_OFF,
    "KAWOOSH_OUT": TL.KAWOOSH_OUT,
    "KAWOOSH_HANG": TL.KAWOOSH_HANG,
    "KAWOOSH_BACK": TL.KAWOOSH_BACK,
    "IRIS_MOVE": TL.IRIS_MOVE,
    "DIAL_END": TL.DIAL_END,
    "T_KAWOOSH": TL.T_KAWOOSH,
    "T_PUDDLE": TL.T_PUDDLE,
    "T_IRIS_CLOSE": TL.T_IRIS_CLOSE,
    "T_IRIS_CLOSED": TL.T_IRIS_CLOSED,
    "T_IRIS_OPEN": TL.T_IRIS_OPEN,
    "T_IRIS_OPENED": TL.T_IRIS_OPENED,
    "T_SHUTDOWN": TL.T_SHUTDOWN,
    "T_DARK": TL.T_DARK,
    "T_CHEVRONS_OFF": TL.T_CHEVRONS_OFF,
}

BODY = r"""
// Chevron housings clockwise from the top (degrees), SG-1 numbering.
var CHEVRON_ANGLES = %(angles)s

// [start, end, fromDeg, toDeg] per ring move; [time, chevron] per lock.
var SPINS = %(spins)s
var LOCKS = %(locks)s

function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x }
function smooth(x) { x = clamp01(x); return x * x * (3 - 2 * x) }
function smoother(x) { x = clamp01(x); return x * x * x * (x * (6 * x - 15) + 10) }

function spinProfile(x) {
  var acc = 0.22, dec = 0.30
  x = clamp01(x)
  var vmax = 1 / (1 - acc / 2 - dec / 2)
  if (x < acc) return vmax * (x / 2 - acc / (2 * Math.PI) * Math.sin(Math.PI * x / acc))
  var sAcc = vmax * acc / 2
  if (x < 1 - dec) return sAcc + vmax * (x - acc)
  var y = x - (1 - dec)
  var sCruise = sAcc + vmax * (1 - dec - acc)
  return sCruise + vmax * (y / 2 + dec / (2 * Math.PI) * Math.sin(Math.PI * y / dec))
}

// Glyph ring rotation, degrees clockwise as seen from the room.
function ringAngle(t) {
  var angle = 0
  for (var i = 0; i < SPINS.length; i++) {
    var s = SPINS[i]
    if (t < s[0]) return angle
    if (t < s[1]) return s[2] + (s[3] - s[2]) * spinProfile((t - s[0]) / (s[1] - s[0]))
    angle = s[3]
  }
  return angle
}

// Index of the ring move in progress, or -1.
function spinning(t) {
  for (var i = 0; i < SPINS.length; i++) if (t >= SPINS[i][0] && t < SPINS[i][1]) return i
  return -1
}

// Glow 0..1 for chevrons 1..9 (index 0 unused).
function chevronLevels(t) {
  var lv = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  var off = t >= T_DARK ? 1 - smooth((t - T_DARK) / CHEVRONS_OFF) : 1
  for (var i = 0; i < LOCKS.length; i++) {
    var onAt = LOCKS[i][0] + LOCK_DOWN * 0.8
    if (t >= onAt) lv[LOCKS[i][1]] = smooth((t - onAt) / 0.08) * off
  }
  return lv
}

function horizonLevel(t) {
  if (t < T_KAWOOSH || t >= T_DARK) return 0
  if (t >= T_SHUTDOWN) return 1 - Math.pow((t - T_SHUTDOWN) / SHUTDOWN, 2.2)
  return 1
}

function horizonFlash(t) {
  var f = 0
  var u = t - T_KAWOOSH
  if (u >= 0 && u < 1.6) f = Math.max(f, Math.exp(-u / 0.35) * smooth(u / 0.05))
  u = t - T_SHUTDOWN
  if (u >= 0 && u < SHUTDOWN)
    f = Math.max(f, 0.6 * Math.sin(Math.PI * clamp01(u / (SHUTDOWN * 0.5))) * (1 - u / SHUTDOWN))
  return f
}

// 0..1 of full kawoosh length.
function kawoosh(t) {
  var u = t - T_KAWOOSH
  if (u < 0 || t >= T_PUDDLE) return 0
  if (u < KAWOOSH_OUT) return 1 - Math.pow(1 - u / KAWOOSH_OUT, 3)
  u -= KAWOOSH_OUT
  if (u < KAWOOSH_HANG) return 1
  u -= KAWOOSH_HANG
  return 1 - smoother(u / KAWOOSH_BACK)
}

// 1 = open, 0 = closed.
function irisOpen(t) {
  if (t < T_IRIS_CLOSE || t >= T_IRIS_OPENED) return 1
  if (t < T_IRIS_CLOSED) return 1 - smoother((t - T_IRIS_CLOSE) / IRIS_MOVE)
  if (t < T_IRIS_OPEN) return 0
  return smoother((t - T_IRIS_OPEN) / IRIS_MOVE)
}

var NUMBERS = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN"]

// Dialing-computer readout: { text, tone } where tone is one of
// idle | dial | lock | vortex | wormhole | iris | alert
function status(t) {
  if (t < SPINS[0][0]) return { text: "GATE IDLE", tone: "idle" }
  if (t < DIAL_END) {
    var locked = 0
    for (var i = 0; i < LOCKS.length; i++) if (t >= LOCKS[i][0]) locked = LOCKS[i][1]
    if (locked === 7) return { text: "CHEVRON SEVEN LOCKED", tone: "lock" }
    if (spinning(t) >= 0) return { text: "ENCODING CHEVRON " + NUMBERS[locked + 1], tone: "dial" }
    return { text: "CHEVRON " + NUMBERS[locked] + " ENCODED", tone: "lock" }
  }
  if (t < T_KAWOOSH) return { text: "CHEVRON SEVEN LOCKED", tone: "lock" }
  if (t < T_PUDDLE) return { text: "UNSTABLE VORTEX", tone: "vortex" }
  if (t < T_IRIS_CLOSE) return { text: "WORMHOLE ESTABLISHED", tone: "wormhole" }
  if (t < T_IRIS_CLOSED) return { text: "CLOSING THE IRIS", tone: "alert" }
  if (t < T_IRIS_OPEN) return { text: "IRIS CLOSED", tone: "iris" }
  if (t < T_IRIS_OPENED) return { text: "OPENING THE IRIS", tone: "alert" }
  if (t < T_SHUTDOWN) return { text: "WORMHOLE ESTABLISHED", tone: "wormhole" }
  if (t < T_CHEVRONS_OFF + 0.4) return { text: "WORMHOLE DISENGAGED", tone: "dial" }
  return { text: "GATE IDLE", tone: "idle" }
}
"""


def main():
    out = sys.argv[1]
    head = [
        ".pragma library",
        "",
        "// GENERATED by stargate-sg1/tools/gen_gate_js.py from sgc_timeline.py --",
        "// edit those, not this. Mirrors the wallpaper loop (gate-loop.mp4) so",
        "// the bar can show what the gate on screen is doing.",
        "",
    ]
    for k, v in CONSTS.items():
        head.append("var %s = %s" % (k, json.dumps(round(v, 6) if isinstance(v, float) else v)))
    spins = [[round(a, 6), round(b, 6), round(c, 6), round(d, 6)] for a, b, c, d in TL.SPINS]
    locks = [[round(a, 6), n] for a, n in TL.LOCKS]
    angles = [0] * 10
    for n, a in TL.CHEVRON_ANGLES.items():
        angles[n] = a
    body = BODY % {"angles": json.dumps(angles), "spins": json.dumps(spins), "locks": json.dumps(locks)}
    with open(out, "w") as fh:
        fh.write("\n".join(head) + "\n" + body)


if __name__ == "__main__":
    main()

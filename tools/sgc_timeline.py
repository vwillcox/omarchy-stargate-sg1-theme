"""Event timeline for the Stargate Command gate-room wallpaper loop.

Shared by sgc_gate.py (Blender, sets the scene up per frame) and
gate_compose.py (post: bloom, flash, grain), so both agree on when things
happen. Pure Python, no numpy/bpy, all times in seconds.

The loop, outgoing dial in the SG-1 manner:
  idle -> inner ring spins and the top chevron encodes each of seven glyphs
  (directions alternate; chevrons light 1,2,3 down the right, 4,5,6 up the
  left, 7 at the top) -> kawoosh -> stable event horizon -> the iris closes
  over the live wormhole, holds, reopens -> the wormhole disengages ->
  chevrons go dark -> idle.

The ring's seven moves are whole glyph steps summing to one full turn, so
it ends exactly where it started and the loop is seamless by construction.
"""
import math

FPS = 30
GLYPHS = 39
STEP_DEG = 360.0 / GLYPHS

# Chevron housings clockwise from the top, as SG-1 numbers them: 7 (master)
# at the top, 1-3 down the right, 8 and 9 at the bottom (unused for a
# seven-symbol address), 4-6 up the left.
CHEVRON_ANGLES = {7: 0, 1: 40, 2: 80, 3: 120, 8: 160, 9: 200, 4: 240, 5: 280, 6: 320}

# Signed glyph steps per chevron (+ = clockwise as seen from the room).
# Alternating directions; the sum is +39 = one full turn.
MOVES = [+17, -14, +19, -13, +21, -16, +25]
assert sum(MOVES) % GLYPHS == 0

IDLE_LEAD = 3.0          # dark gate before dialling starts
LOCK_DOWN = 0.22         # master chevron slides in
LOCK_HOLD = 0.38
LOCK_UP = 0.30
LOCK_PAUSE = 0.30        # before the ring moves again
KAWOOSH_DELAY = 0.55     # chevron 7 locked -> vortex
KAWOOSH_OUT = 0.36       # vortex reaches full length
KAWOOSH_HANG = 0.18
KAWOOSH_BACK = 0.80      # collapses back into the puddle
KAWOOSH_LEN = 5.6        # metres out of the gate plane
EH_BEFORE_IRIS = 8.5
IRIS_MOVE = 2.2
IRIS_HOLD = 4.0
EH_AFTER_IRIS = 4.8
SHUTDOWN = 0.85          # puddle collapses
CHEVRONS_OFF = 0.35      # after the puddle is gone, chevrons fade out
IDLE_TAIL = 2.6


def spin_time(steps):
    return 0.95 + 0.082 * abs(steps)


# ----------------------------------------------------------------- schedule
_t = IDLE_LEAD
SPINS = []        # (start, end, angle_from_deg, angle_to_deg)
LOCKS = []        # (start, chevron_number)
_angle = 0.0
for k, m in enumerate(MOVES):
    d = spin_time(m)
    SPINS.append((_t, _t + d, _angle, _angle + m * STEP_DEG))
    _angle += m * STEP_DEG
    _t += d
    LOCKS.append((_t, k + 1))
    _t += LOCK_DOWN + LOCK_HOLD + LOCK_UP + (LOCK_PAUSE if k < 6 else 0.0)
DIAL_END = LOCKS[-1][0] + LOCK_DOWN
T_KAWOOSH = DIAL_END + KAWOOSH_DELAY
T_PUDDLE = T_KAWOOSH + KAWOOSH_OUT + KAWOOSH_HANG + KAWOOSH_BACK
T_IRIS_CLOSE = T_KAWOOSH + EH_BEFORE_IRIS
T_IRIS_CLOSED = T_IRIS_CLOSE + IRIS_MOVE
T_IRIS_OPEN = T_IRIS_CLOSED + IRIS_HOLD
T_IRIS_OPENED = T_IRIS_OPEN + IRIS_MOVE
T_SHUTDOWN = T_IRIS_OPENED + EH_AFTER_IRIS
T_DARK = T_SHUTDOWN + SHUTDOWN
T_CHEVRONS_OFF = T_DARK + CHEVRONS_OFF
DURATION = T_CHEVRONS_OFF + IDLE_TAIL
FRAMES = int(round(DURATION * FPS))
DURATION = FRAMES / FPS


# ------------------------------------------------------------------ easings
def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def smooth(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def smoother(x):
    x = clamp01(x)
    return x * x * x * (x * (6 * x - 15) + 10)


def ease_out_cubic(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def spin_profile(x, acc=0.22, dec=0.30):
    """Distance covered (0..1) for a raised-cosine accel / cruise / decel
    velocity profile over normalised time x; acc/dec are fractions of the move."""
    x = clamp01(x)
    vmax = 1.0 / (1.0 - acc / 2 - dec / 2)
    if x < acc:
        # integral of vmax*(1-cos(pi*s/acc))/2
        return vmax * (x / 2 - acc / (2 * math.pi) * math.sin(math.pi * x / acc))
    s_acc = vmax * acc / 2
    if x < 1 - dec:
        return s_acc + vmax * (x - acc)
    y = x - (1 - dec)
    s_cruise = s_acc + vmax * (1 - dec - acc)
    return s_cruise + vmax * (y / 2 + dec / (2 * math.pi) * math.sin(math.pi * y / dec))


# ------------------------------------------------------------------- state
def ring_angle(t):
    """Inner (glyph) ring rotation in degrees, clockwise from the room."""
    angle = 0.0
    for s0, s1, a0, a1 in SPINS:
        if t < s0:
            return angle
        if t < s1:
            return a0 + (a1 - a0) * spin_profile((t - s0) / (s1 - s0))
        angle = a1
    # unwrapped (ends at +360): keyframes must not jump, motion blur would streak
    return angle


def master_travel(t):
    """0..1: how far the top (master) chevron has slid toward the centre."""
    for t0, n in LOCKS:
        u = t - t0
        if u < 0:
            continue
        if n == 7:
            if t >= T_DARK:
                return 1 - smooth((t - T_DARK) / 0.5)
            return smooth(u / LOCK_DOWN)
        if u < LOCK_DOWN:
            return smooth(u / LOCK_DOWN)
        if u < LOCK_DOWN + LOCK_HOLD:
            return 1.0
        if u < LOCK_DOWN + LOCK_HOLD + LOCK_UP:
            return 1 - smooth((u - LOCK_DOWN - LOCK_HOLD) / LOCK_UP)
    return 0.0


def chevron_levels(t):
    """{chevron number: glow 0..1}. A chevron lights the moment the master
    chevron bottoms out on its glyph, and they all go dark together after
    the wormhole disengages."""
    lv = {n: 0.0 for n in CHEVRON_ANGLES}
    off = 1 - smooth((t - T_DARK) / CHEVRONS_OFF) if t >= T_DARK else 1.0
    for t0, n in LOCKS:
        on_at = t0 + LOCK_DOWN * 0.8
        if t >= on_at:
            lv[n] = smooth((t - on_at) / 0.08) * off
    # the master chevron glows while it is engaged, then holds after #7
    mt = master_travel(t)
    lv[7] = max(lv[7], mt if t < T_DARK else lv[7])
    return lv


def kawoosh_length(t):
    u = t - T_KAWOOSH
    if u < 0 or t >= T_PUDDLE:
        return 0.0
    if u < KAWOOSH_OUT:
        return KAWOOSH_LEN * ease_out_cubic(u / KAWOOSH_OUT)
    u -= KAWOOSH_OUT
    if u < KAWOOSH_HANG:
        return KAWOOSH_LEN * (1 + 0.04 * math.sin(math.pi * u / KAWOOSH_HANG))
    u -= KAWOOSH_HANG
    return KAWOOSH_LEN * (1 - smoother(u / KAWOOSH_BACK))


def horizon_level(t):
    """Event horizon presence 0..1 (radius scale while collapsing)."""
    if t < T_KAWOOSH or t >= T_DARK:
        return 0.0
    if t >= T_SHUTDOWN:
        x = (t - T_SHUTDOWN) / SHUTDOWN
        return 1 - x ** 2.2   # accelerates inward, like the show's suck-back
    return 1.0


def horizon_turbulence(t):
    """Ripple agitation: violent right after the kawoosh, settling to a calm
    shimmer, kicking up again as the wormhole disengages."""
    if t < T_KAWOOSH or t >= T_DARK:
        return 0.0
    calm = 0.28
    after = math.exp(-max(0.0, t - T_PUDDLE) / 1.4)
    kick = 1.0 if t < T_PUDDLE else calm + (1 - calm) * after
    if t >= T_SHUTDOWN:
        kick = max(kick, 0.6 + 0.4 * clamp01((t - T_SHUTDOWN) / SHUTDOWN))
    return kick


def horizon_flash(t):
    """Extra brightness: the kawoosh burst and the brief surge at shutdown."""
    f = 0.0
    u = t - T_KAWOOSH
    if 0 <= u < 1.6:
        f = max(f, math.exp(-u / 0.35) * smooth(u / 0.05))
    u = t - T_SHUTDOWN
    if 0 <= u < SHUTDOWN:
        f = max(f, 0.6 * math.sin(math.pi * clamp01(u / (SHUTDOWN * 0.5))) * (1 - u / SHUTDOWN))
    return f


def iris_open(t):
    """1 = fully open (blades retracted into the gate), 0 = closed."""
    if t < T_IRIS_CLOSE or t >= T_IRIS_OPENED:
        return 1.0
    if t < T_IRIS_CLOSED:
        return 1 - smoother((t - T_IRIS_CLOSE) / IRIS_MOVE)
    if t < T_IRIS_OPEN:
        return 0.0
    return smoother((t - T_IRIS_OPEN) / IRIS_MOVE)


def state_key(t):
    """Hashable summary of everything visible at time t; frames with equal
    keys render identically, so only the first of each run is rendered."""
    lv = chevron_levels(t)
    animated_eh = horizon_level(t) > 0 and iris_open(t) > 0
    return (
        round(ring_angle(t) % 360.0, 4),
        round(master_travel(t), 4),
        tuple(round(lv[n], 4) for n in sorted(lv)),
        round(kawoosh_length(t), 4),
        round(horizon_level(t), 4),
        round(iris_open(t), 4),
        # a live, visible horizon shimmers every frame
        round(t, 4) if animated_eh else None,
        # camera shake after the kawoosh
        round(t, 4) if 0 < t - T_KAWOOSH < 1.8 else None,
    )


if __name__ == "__main__":
    print(f"loop {DURATION:.2f} s = {FRAMES} frames @ {FPS} fps")
    for name in ("DIAL_END", "T_KAWOOSH", "T_PUDDLE", "T_IRIS_CLOSE", "T_IRIS_CLOSED",
                 "T_IRIS_OPEN", "T_IRIS_OPENED", "T_SHUTDOWN", "T_DARK", "T_CHEVRONS_OFF"):
        print(f"  {name:15s} {globals()[name]:6.2f} s  frame {globals()[name] * FPS:7.1f}")
    keys = [state_key(f / FPS) for f in range(FRAMES)]
    unique = sum(1 for i, k in enumerate(keys) if i == 0 or k != keys[i - 1])
    print(f"  distinct consecutive states: {unique} of {FRAMES}")
    print(f"  ring end angle {ring_angle(DURATION - 1e-6):.3f}  (start {ring_angle(0):.3f})")

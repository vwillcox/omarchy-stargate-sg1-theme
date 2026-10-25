#!/usr/bin/env python3
"""Post for the Stargate Command gate-room loop rendered by sgc_gate.py.

Reads the linear-light EXR frames, adds bloom (so the event horizon, the
kawoosh and the lit chevrons glow), a gentle vignette and a hue-preserving
filmic tone map, then pipes the frames to ffmpeg. Frames the renderer held
(identical state) are taken from OUT/manifest.json's "source" map, so the
encoder still gets every frame of the loop.

Usage:
  gate_compose.py FRAMES_DIR OUT.mp4 [--still OUT.png --still-frame N]
                  [--look FILE.exr OUT.png] [--exposure E] [--workers N]
"""
import argparse
import json
import os
import subprocess
import sys
from multiprocessing import Pool

import numpy as np
import OpenEXR

ap = argparse.ArgumentParser()
ap.add_argument("frames_dir", nargs="?")
ap.add_argument("out", nargs="?")
ap.add_argument("--still")
ap.add_argument("--still-frame", type=int, default=None)
ap.add_argument("--look", nargs=2, metavar=("EXR", "PNG"), action="append")
ap.add_argument("--exposure", type=float, default=1.0)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--crf", type=int, default=17)
A = ap.parse_args()


def load(path):
    with OpenEXR.File(path) as f:
        ch = f.channels()
        if "RGB" in ch:
            img = ch["RGB"].pixels
        else:
            img = np.stack([ch[c].pixels for c in "RGB"], axis=-1)
    return np.ascontiguousarray(img, dtype=np.float32)


def box_blur(img, r, axis):
    if r < 1:
        return img
    pad = [(0, 0)] * img.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(img, pad, mode="edge"), axis=axis, dtype=np.float32)
    hi = [slice(None)] * img.ndim
    lo = [slice(None)] * img.ndim
    hi[axis] = slice(2 * r + 1, None)
    lo[axis] = slice(0, -(2 * r + 1))
    return (c[tuple(hi)] - c[tuple(lo)]) / (2 * r + 1)


def gblur(img, r, passes=3):
    for _ in range(passes):
        img = box_blur(box_blur(img, r, 0), r, 1)
    return img


def down(img, f):
    h, w = img.shape[0] // f, img.shape[1] // f
    return img[:h * f, :w * f].reshape(h, f, w, f, -1).mean(axis=(1, 3))


def up(img, f, shape):
    big = np.repeat(np.repeat(img, f, axis=0), f, axis=1)
    out = np.zeros(shape, np.float32)
    out[:big.shape[0], :big.shape[1]] = big
    return out


def bloom(img):
    """Energy-ish preserving glow: a soft wide halo plus a tighter core, from
    everything above a low threshold (the room is dim; the gate's light is
    what should spill)."""
    bright = np.clip(img - 0.6, 0, None)
    small = down(bright, 4)
    acc = np.zeros_like(small)
    for r, w in ((2, 0.55), (6, 0.35), (16, 0.22), (40, 0.14), (90, 0.08)):
        acc += gblur(small, r) * w
    big = box_blur(box_blur(up(acc, 4, img.shape), 3, 0), 3, 1)
    return img + big * 0.45


H, W = 1440, 3440
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
rad = np.hypot((xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2) * 0.62)
VIGNETTE = np.clip(1.08 - 0.32 * rad ** 2, 0.55, 1.0).astype(np.float32)[..., None]
# Static dither (same every frame, so held frames stay identical and encode
# to nothing): breaks up 8-bit banding in the dark blue gradients.
DITHER = (np.random.default_rng(9).random((H, W, 1), np.float32) - 0.5) / 255.0
del yy, xx, rad


def grade(img, exposure):
    h, w = img.shape[:2]
    img = img * exposure
    img = bloom(img)
    vig = VIGNETTE if (h, w) == (H, W) else 1.0
    img = img * vig
    # Hue-preserving shoulder: tone-map the max channel and scale RGB by it,
    # so the horizon stays blue and the chevrons orange instead of bleaching;
    # only the very hottest cores drift toward white.
    m = np.maximum(img.max(axis=-1, keepdims=True), 1e-6)
    tm = 1 - np.exp(-m * 1.15)
    hue = img * (tm / m)
    white = np.clip((m - 2.0) / 5.0, 0, 0.65)
    img = hue * (1 - white) + tm * white
    out = np.clip(img, 0, 1) ** (1 / 2.2)
    if (h, w) == (H, W):
        out = out + DITHER
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def write_png(img, path):
    h, w = img.shape[:2]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", "%dx%d" % (w, h), "-i", "-", path], input=img.tobytes(), check=True)


def compose(src):
    return grade(load(os.path.join(A.frames_dir, "sgc_%04d.exr" % src)), A.exposure)


if __name__ == "__main__":
    if A.look:
        for exr, png in A.look:
            write_png(grade(load(exr), A.exposure), png)
        sys.exit(0)

    with open(os.path.join(A.frames_dir, "manifest.json")) as fh:
        MAN = json.load(fh)
    source = MAN["source"]
    T = MAN["frames"]
    if A.still_frame is not None:
        write_png(compose(source[A.still_frame]), A.still)
        sys.exit(0)

    order = []
    for s in source:
        if s not in order:
            order.append(s)
    # frame size from the EXRs themselves: preview renders are scaled down
    with OpenEXR.File(os.path.join(A.frames_dir, "sgc_%04d.exr" % order[0])) as f:
        fh, fw = f.channels()["RGB"].pixels.shape[:2]
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", "%dx%d" % (fw, fh), "-r", str(MAN["fps"]), "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(A.crf), "-tune", "film",
        "-pix_fmt", "yuv420p", "-g", str(MAN["fps"] * 2), "-movflags", "+faststart", A.out,
    ], stdin=subprocess.PIPE)
    done = {}
    with Pool(A.workers) as pool:
        results = pool.imap(compose, order, chunksize=1)
        for f in range(T):
            s = source[f]
            while s not in done:
                k = order[len(done)]
                done[k] = next(results)
                # keep frame 0 (the loop's tail reuses it) and the latest
                for old in [x for x in done if x not in (0, k)]:
                    done[old] = None
            enc.stdin.write(done[s].tobytes())
            print("frame %d/%d (source %d)" % (f + 1, T, s), flush=True)
    enc.stdin.close()
    enc.wait()

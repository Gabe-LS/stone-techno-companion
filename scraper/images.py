from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

import pyvips

from .db import get_artists_missing_photos, save_photo_local

SSIMULACRA2_TARGET = 78.0


def post_downscale_sharpen(im: pyvips.Image, scale: float) -> pyvips.Image:
    if scale >= 1.0:
        return im
    t = min(1.0 - scale, 1.0)
    sigma = 0.5 + 0.3 * t
    m1 = 0.1
    m2 = 0.15 + 0.25 * t
    return im.sharpen(sigma=sigma, x1=1.0, y2=10, y3=20, m1=m1, m2=m2)


def resize_and_crop(raw_path: str, size: int) -> pyvips.Image:
    im = pyvips.Image.new_from_file(raw_path)
    im = im.autorot()
    if im.hasalpha():
        im = im.flatten(background=[255, 255, 255])
    src_w, src_h = im.width, im.height
    scale = size / min(src_w, src_h)
    resized = im.resize(scale, kernel="lanczos3")
    if resized.width > size or resized.height > size:
        left = (resized.width - size) // 2
        top = (resized.height - size) // 2
        resized = resized.crop(left, top, size, size)
    return post_downscale_sharpen(resized, scale)


def encode_avif_to_target(ref_im: pyvips.Image, out_path: str, target: float) -> float:
    with tempfile.TemporaryDirectory() as tmp_dir:
        ref_png = f"{tmp_dir}/ref.png"
        ref_im.pngsave(ref_png, compression=1)
        lo, hi = 20, 80
        best_q = 60
        while hi - lo > 1:
            mid = (lo + hi) // 2
            candidate = f"{tmp_dir}/candidate.avif"
            ref_im.heifsave(
                candidate, Q=mid, compression=pyvips.enums.ForeignHeifCompression.AV1
            )
            decoded = f"{tmp_dir}/decoded.png"
            subprocess.run(
                ["vips", "copy", candidate, decoded], check=True, capture_output=True
            )
            result = subprocess.run(
                ["ssimulacra2", ref_png, decoded],
                check=True,
                capture_output=True,
                text=True,
            )
            score = float(result.stdout.strip())
            if score < target:
                lo = mid
            else:
                hi = mid
                best_q = mid
        ref_im.heifsave(
            out_path, Q=best_q, compression=pyvips.enums.ForeignHeifCompression.AV1
        )
        decoded = f"{tmp_dir}/final_decoded.png"
        subprocess.run(
            ["vips", "copy", out_path, decoded], check=True, capture_output=True
        )
        result = subprocess.run(
            ["ssimulacra2", ref_png, decoded],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())


def process_artist_photos(db: sqlite3.Connection, photos_dir: Path) -> None:
    missing = get_artists_missing_photos(db)
    if not missing:
        print("All photos already processed.")
        return
    photos_dir.mkdir(parents=True, exist_ok=True)
    total = len(missing)
    print(f"Processing {total} artist photos ...")
    for i, (overlay_id, photo_url) in enumerate(missing, 1):
        name = (
            db.execute(
                "SELECT name FROM artists WHERE overlay_id = ?", (overlay_id,)
            ).fetchone()
            or [overlay_id]
        )[0]
        print(f"  [{i}/{total}] {name}", end="", flush=True)
        filename = f"{overlay_id}.avif"
        out_path = photos_dir / filename
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                raw_path = f"{tmp_dir}/original"
                req = Request(photo_url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=30) as resp:
                    with open(raw_path, "wb") as f:
                        f.write(resp.read())
                ref_im = resize_and_crop(raw_path, 240)
                score = encode_avif_to_target(ref_im, str(out_path), SSIMULACRA2_TARGET)
            save_photo_local(db, overlay_id, filename)
            print(f" -> {out_path.stat().st_size / 1024:.1f}KB ssim2={score:.1f}")
        except Exception as e:
            print(f" -> ERROR: {e}")

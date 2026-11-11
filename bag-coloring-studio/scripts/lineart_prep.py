"""线稿预处理：把"生成出来的线稿"整理成可分区的基础稿。

用法：
  python3 lineart_prep.py --image gen.png --out work/lineart.png --seal 1

输出 JSON：墨迹占比、闭合区域数、背景占比、最大区域占比，供 qc_check 判定。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np


def read_image(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit("读不了图：%s" % path)
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * alpha + 255 * (1 - alpha)).astype(np.uint8)
    return img


def analyze(ink):
    """ink: 0/255，255=墨迹。返回闭合性与分区潜力指标。"""
    h, w = ink.shape
    total = h * w
    free = (ink == 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, 8)
    border = set(np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])))
    bg = max(border, key=lambda i: stats[i, cv2.CC_STAT_AREA]) if border else 0
    bg_pct = float(stats[bg, cv2.CC_STAT_AREA]) / total * 100 if border else 0.0
    inside = [i for i in range(1, n) if i not in border and stats[i, cv2.CC_STAT_AREA] >= 100]
    max_pct = max([float(stats[i, cv2.CC_STAT_AREA]) / total * 100 for i in inside] or [0])
    return {
        "ink_pct": round(float((ink > 0).mean()) * 100, 2),
        "bg_pct": round(bg_pct, 1),
        "closed_regions": len(inside),
        "max_region_pct": round(max_pct, 1),
        "leak_suspect": bg_pct < 18.0,
    }


def clean(args):
    img = read_image(args.image)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if args.max_side and max(gray.shape) > args.max_side:
        s = args.max_side / max(gray.shape)
        gray = cv2.resize(gray, (int(gray.shape[1] * s), int(gray.shape[0] * s)),
                          interpolation=cv2.INTER_AREA)
    thr, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if args.despeckle > 0:
        n, lab, stats, _ = cv2.connectedComponentsWithStats((ink > 0).astype(np.uint8), 8)
        keep = np.zeros_like(ink)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= args.despeckle:
                keep[lab == i] = 255
        ink = keep
    if args.seal > 0:
        k = np.ones((args.seal * 2 + 1, args.seal * 2 + 1), np.uint8)
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k)   # 补 1–2px 断口
    out = np.full(gray.shape, 255, np.uint8)
    out[ink > 0] = 0
    cv2.imwrite(args.out, out)
    return analyze(ink), thr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--despeckle", type=int, default=24, help="小于该面积的墨点视为噪点")
    ap.add_argument("--seal", type=int, default=1, help="补断口的核半径，0 为不补")
    ap.add_argument("--max-side", type=int, default=2048)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    stats, thr = clean(args)
    stats.update({"out": args.out, "otsu": int(thr)})
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

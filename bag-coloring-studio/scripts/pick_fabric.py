"""自动挑初始布样：在包里找一块"最像纯布面"的方块，替代人眼反复裁框。

背景：一贴布就想给页面预置一块用户的布，但"从照片里裁一块干净布"以前靠人眼：裁一框、打开看、
发现压到字/沙粒/星点、再裁一框。沙滩照实测裁了 5 框才对，这是整单最慢的一环。

做法：在给定范围内滑窗，按"少纹理、少彩度、亮度不过暗/过曝"打分，取最低分那块。
  - 文字/印花：边缘密度高、彩度高 → 被扣分
  - 沙粒/木纹/编织纹：高频纹理，边缘密度高 → 被扣分
  - 深投影/高光：亮度偏离舒适区 → 被扣分
  - 纯布面（带一点柔和褶）：边缘稀疏、彩度低 → 得分最好

用法：
  python3 pick_fabric.py --image bag.jpg --out canvas.jpg
  python3 pick_fabric.py --image bag.jpg --out canvas.jpg --bbox 300,1000,900,700
  python3 pick_fabric.py --image bag.jpg --out canvas.jpg --preview p.png   # 也存一张带框的预览

退出码：0 找到；1 没找到可信窗口；2 读图失败。
"""
import argparse
import json
import os

import cv2
import numpy as np


def score_patch(gray, sat, patch_box):
    x, y, s, _ = patch_box
    g = gray[y:y + s, x:x + s]
    sa = sat[y:y + s, x:x + s]
    edges = cv2.Canny(g, 60, 160)
    edge = float((edges > 0).mean())              # 边缘密度：文字/砂粒/木纹都高
    luma = float(g.mean())
    gstd = float(g.std())
    sat_mean = float(sa.mean()) / 255.0
    sat_std = float(sa.std()) / 255.0
    # 亮度舒适区 150–230（白布偏亮），偏离就罚；过曝（>245）、阴影像素同样罚
    luma_pen = abs(luma - 205) / 255.0
    over = float((g > 246).mean())                # 大片死白=拍到高光，贴上去发亮
    # 暗像素占比：布面文字/印花/缝线投影都是"布面上的一小块深色"，这是最直接的脏信号
    dark = float((g < 120).mean())
    score = (edge * 8.0 + sat_std * 4.0 + sat_mean * 2.0
             + luma_pen * 1.0 + gstd / 255.0 * 3.0 + over * 4.0 + dark * 10.0)
    return {"x": x, "y": y, "size": s, "score": round(score, 4), "edge": round(edge, 4),
            "luma": round(luma, 1), "luma_std": round(gstd, 1), "dark_frac": round(dark, 4),
            "sat_mean": round(sat_mean, 3), "sat_std": round(sat_std, 3), "overexposed": round(over, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True, help="输出布样（正方形 jpg/png）")
    ap.add_argument("--bbox", default=None, help="只在照片的这个矩形里找：x,y,w,h（一般传包身范围）")
    ap.add_argument("--size", type=int, default=480, help="输出边长")
    ap.add_argument("--window", type=int, default=260, help="滑窗边长（源图像素）；太大就会把文字/边线稀释进评分")
    ap.add_argument("--stride", type=float, default=0.25, help="滑窗步长占窗口比例")
    ap.add_argument("--preview", default=None, help="存一张带选中框的缩小预览")
    ap.add_argument("--pattern", action="store_true",
                    help="印花/条纹/编织包：布样本来就有花色，放宽彩度与边缘判据（只排除死白与深黑）")
    args = ap.parse_args()

    img = cv2.imread(args.image, cv2.IMREAD_UNCHANGED)
    if img is None:
        print(json.dumps({"ok": False, "error": "读不了图：%s" % args.image}, ensure_ascii=False))
        return 2
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * a + 255 * (1 - a)).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    H, W = img.shape[:2]

    if args.bbox:
        bx, by, bw, bh = [int(v) for v in args.bbox.split(",")]
    else:
        bx, by, bw, bh = 0, 0, W, H
    bx, by = max(0, bx), max(0, by)
    bw, bh = min(bw, W - bx), min(bh, H - by)
    win = min(args.window, bw, bh)
    stride = max(8, int(win * args.stride))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1]

    best = None
    for y in range(by, by + bh - win + 1, stride):
        for x in range(bx, bx + bw - win + 1, stride):
            p = score_patch(gray, sat, (x, y, win, win))
            if best is None or p["score"] < best["score"]:
                best = p
    if best is None:
        print(json.dumps({"ok": False, "error": "范围太小，放不下窗口"}, ensure_ascii=False))
        return 1

    patch = img[best["y"]:best["y"] + win, best["x"]:best["x"] + win]
    patch = cv2.resize(patch, (args.size, args.size), interpolation=cv2.INTER_LANCZOS4)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    cv2.imwrite(args.out, patch)

    if args.preview:
        vis = img.copy()
        cv2.rectangle(vis, (best["x"], best["y"]), (best["x"] + win, best["y"] + win), (0, 0, 255), 4)
        s = 640 / max(H, W)
        vis = cv2.resize(vis, (int(W * s), int(H * s)))
        cv2.imwrite(args.preview, vis)

    if args.pattern:
        ok = best["dark_frac"] < 0.06 and best["overexposed"] < 0.25
    else:
        ok = best["edge"] < 0.10 and best["sat_std"] < 0.22 and best["dark_frac"] < 0.02
    best.update({"ok": ok, "mode": "pattern" if args.pattern else "plain",
                 "out": args.out, "window": win,
                 "crop": [best["x"], best["y"], win, win]})
    if not ok:
        best["warning"] = "这块仍然偏杂（死白或深黑占比高）：照片里可能没有合适布面，考虑不预置布样"
    print(json.dumps(best, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""照片去背 + 裁边（可选工具，不进主流程，默认不推荐）。

用途只有一个：用户明确要"把包抠成透明图 / 要透明素材"时。常规流程**不要用它去预处理要喂给
生成模型的照片**——边界一旦咬进包体（提手内侧、包底暗部），生成模型会照着缺角的图作画，
错误会被放大成线稿里的结构缺失；而照片背景脏的正解是在提示词里换背景（"纯白背景、不要桌面
与投影阴影"），零成本。

它的有效区间和需求区间正好相反：背景与主体色差大时抠得干净（那时本也不需要去背），背景与主体
同色时必然留残影（深色桌面 + 藏青条纹实测：右侧投影被算成主体，凸包外像素 57%），调参无解。

流程：GrabCut 抠出主体 → 裁边 → 出一张"原图 / 去背白底 / 掩膜叠加"的三联对照预览。
判定不靠颜色猜测：脚本只报客观指标，最终由预览图 + 用户确认决定用不用。

用法：
  python3 debg.py --image bag.jpg --out work/debg
  python3 debg.py --image bag.jpg --out work/debg --rect 120,60,1500,1100 --bg "10,10;1500,20" --fg "900,700"

产出：
  <out>_white.png   白底裁边图（去背成功后喂给 image_edit 生成线稿）
  <out>_cut.png     RGBA 透明底裁边图（给降级档掩膜、给设计软件）
  <out>_preview.png 三联对照（原图 / 白底 / 掩膜边界），必须发给用户看
  <out>.json        指标与告警

verdict：
  ok       指标正常，但**仍要发 preview 让用户确认**再拿去生成
  risky    指标异常（主体被吞 / 背景没切掉 / 边界贴住画面边缘），_white 已退回成原图，
           不要当已去背使用；改走"提示词要求纯白背景"，或补 --bg/--fg 笔、收紧 --rect 重跑

为什么必须在 agent 侧跑：浏览器端 opencv.js 的 cv.grabCut 实测接口坏（7083008 / 7083328），
SAM 权重 13.8MB + wasm 10MB 级，塞进单文件页面不现实。

已知边界（实测）：背景与主体同色时（深色桌面 + 藏青条纹包，色差仅 5–8/255），GrabCut 会把同色部位
一起吃掉，这不是参数问题。这类图的正解是让生成模型换背景（提示词写"纯白背景、不要桌面与阴影"）。
"""
import argparse
import json
import os

import cv2
import numpy as np


def read_image(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit("读不了图：%s" % path)
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * alpha + 255 * (1 - alpha)).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img


def parse_points(s):
    pts = []
    if not s:
        return pts
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        x, y = [int(float(v)) for v in chunk.split(",")]
        pts.append((x, y))
    return pts


def border_color(img, band_ratio=0.02):
    h, w = img.shape[:2]
    band = max(3, int(min(h, w) * band_ratio))
    px = np.concatenate([img[:band].reshape(-1, 3), img[-band:].reshape(-1, 3),
                         img[:, :band].reshape(-1, 3), img[:, -band:].reshape(-1, 3)])
    return np.median(px, axis=0).astype(np.float32)


def grabcut(img, rect, bg_pts, fg_pts, iters):
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(img, mask, rect, bgd, fgd, iters, cv2.GC_INIT_WITH_RECT)
    if bg_pts or fg_pts:
        for (x, y) in bg_pts:
            cv2.circle(mask, (int(x), int(y)), 10, int(cv2.GC_BGD), -1)
        for (x, y) in fg_pts:
            cv2.circle(mask, (int(x), int(y)), 10, int(cv2.GC_FGD), -1)
        cv2.grabCut(img, mask, None, bgd, fgd, max(1, iters // 2), cv2.GC_INIT_WITH_MASK)
    return np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)


def clean_mask(fg, keep_holes=False):
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats((fg > 0).astype(np.uint8), 8)
    if n <= 1:
        return fg
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    fg = np.where(lab == biggest, 255, 0).astype(np.uint8)
    if keep_holes:
        return fg
    contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(fg)
    cv2.drawContours(filled, contours, -1, 255, -1)
    return filled


def make_preview(orig, white, fg, scale_to=560):
    h, w = orig.shape[:2]
    s = scale_to / float(max(h, w))
    th, tw = max(1, int(h * s)), max(1, int(w * s))
    a = cv2.resize(orig, (tw, th), interpolation=cv2.INTER_AREA)
    b = cv2.resize(white, (tw, th), interpolation=cv2.INTER_AREA)
    over = a.copy()
    m = cv2.resize(fg, (tw, th), interpolation=cv2.INTER_NEAREST)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(over, cnts, -1, (120, 40, 220), 2)
    out = np.full((th, tw * 3 + 20, 3), 240, np.uint8)
    out[:, :tw] = a
    out[:, tw + 10:tw * 2 + 10] = b
    out[:, tw * 2 + 20:] = over
    try:
        from PIL import Image, ImageDraw, ImageFont
        font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        if not os.path.exists(font_path):
            font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        pil = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        font = ImageFont.truetype(font_path, 16)
        for i, t in enumerate(("原图", "去背白底", "掩膜边界")):
            draw.text((i * (tw + 10) + 6, 4), t, fill=(40, 40, 40), font=font)
        out = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        for i, t in enumerate(("origin", "cutout", "mask")):
            cv2.putText(out, t, (i * (tw + 10) + 6, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.46, (40, 40, 40), 1, cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rect", default=None, help="x,y,w,h；不填用整图内缩 2%")
    ap.add_argument("--bg", default="", help="额外背景笔 x,y;x,y")
    ap.add_argument("--fg", default="", help="额外前景笔 x,y")
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--no-crop", action="store_true")
    ap.add_argument("--keep-holes", action="store_true")
    ap.add_argument("--pad", type=float, default=0.02)
    ap.add_argument("--seg-side", type=int, default=900, help="分割在缩略图上跑，掩膜再放大")
    ap.add_argument("--max-side", type=int, default=2048)
    a = ap.parse_args()

    img = read_image(a.image)
    h0, w0 = img.shape[:2]
    if max(h0, w0) > a.max_side:
        s = a.max_side / float(max(h0, w0))
        img = cv2.resize(img, (int(w0 * s), int(h0 * s)), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]

    s = min(1.0, a.seg_side / float(max(h, w)))
    small = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    if a.rect:
        x, y, rw, rh = [int(v) for v in a.rect.split(",")]
        rect = (int(x * s), int(y * s), int(rw * s), int(rh * s))
        crop_rect = (x, y, rw, rh)
    else:
        m = max(2, int(min(sh, sw) * 0.02))
        rect = (m, m, sw - 2 * m, sh - 2 * m)
        crop_rect = (0, 0, w, h)

    fg_s = clean_mask(grabcut(small, rect, parse_points(a.bg), parse_points(a.fg), a.iters),
                      a.keep_holes)
    fg = clean_mask(cv2.resize(fg_s, (w, h), interpolation=cv2.INTER_NEAREST), a.keep_holes)

    fg_pct = float((fg > 0).mean()) * 100
    band = max(2, int(min(h, w) * 0.02))
    touch = float(np.mean([fg[:band].mean() / 255.0, fg[-band:].mean() / 255.0,
                           fg[:, :band].mean() / 255.0, fg[:, -band:].mean() / 255.0]))
    bg_color = border_color(img)
    d = np.sqrt(((img.astype(np.float32) - bg_color) ** 2).sum(2))
    same_as_bg = float(((fg > 0) & (d < 20)).sum()) / max(1.0, float((fg > 0).sum())) * 100

    # 掩膜落在"主体高对比区凸包"外的比例：高说明背景/阴影被当成主体了
    bright = (d > 60).astype(np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bright, 8)
    if n > 1:
        bright = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    bright_pct = float(bright.mean()) * 100
    outside_pct = 0.0
    if bright_pct >= 8 and (fg > 0).sum() > 0:
        cnts, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = np.zeros_like(bright)
        cv2.drawContours(hull, cnts, -1, 255, -1)
        k = max(5, int(min(h, w) * 0.06) | 1)
        hull = cv2.dilate(hull, np.ones((k, k), np.uint8))
        outside_pct = float(((fg > 0) & (hull == 0)).sum()) / float((fg > 0).sum()) * 100

    reasons = []
    if fg_pct < 8:
        reasons.append("前景只剩 %.1f%%，主体几乎被当成背景" % fg_pct)
    if fg_pct > 75:
        reasons.append("前景占 %.1f%%，背景基本没切掉" % fg_pct)
    if touch > 0.03:
        reasons.append("掩膜贴住画面边缘（边带 %.0f%% 被判成主体），多半是背景被算进去了" % (touch * 100))

    if reasons:
        fg = np.full((h, w), 255, np.uint8)
        x0, y0 = crop_rect[0], crop_rect[1]
        x1, y1 = min(w, crop_rect[0] + crop_rect[2]), min(h, crop_rect[1] + crop_rect[3])
        verdict = "risky"
    else:
        ys, xs = np.where(fg > 0)
        pad_x = int((xs.max() - xs.min() + 1) * a.pad) + 2
        pad_y = int((ys.max() - ys.min() + 1) * a.pad) + 2
        x0, y0 = max(0, int(xs.min()) - pad_x), max(0, int(ys.min()) - pad_y)
        x1, y1 = min(w, int(xs.max()) + pad_x + 1), min(h, int(ys.max()) + pad_y + 1)
        verdict = "ok"

    alpha = cv2.GaussianBlur((fg > 0).astype(np.uint8) * 255, (3, 3), 0)
    rgba = np.dstack([img, alpha])
    white = img.copy()
    white[alpha < 250] = (255, 255, 255)

    if a.no_crop:
        cut, wb, box = rgba, white, [0, 0, w, h]
    else:
        cut, wb = rgba[y0:y1, x0:x1], white[y0:y1, x0:x1]
        box = [int(x0), int(y0), int(x1), int(y1)]

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    cut_path, white_path = a.out + "_cut.png", a.out + "_white.png"
    preview_path = a.out + "_preview.png"
    cv2.imwrite(cut_path, cut)
    cv2.imwrite(white_path, wb)
    cv2.imwrite(preview_path, make_preview(img, white, fg))

    warn = list(reasons)
    if verdict == "risky":
        warn.append("指标异常，_white 已退回成原图，不要当已去背使用。改为在生成提示词里写"
                    "「纯白背景、不要桌面与阴影」让生成模型换背景；或补 --bg/--fg 笔、收紧 --rect 重跑。")
    if same_as_bg >= 15 and verdict == "ok":
        warn.append("主体里有 %.0f%% 的像素与背景色差极小（<20/255），可能把同色部位当成了背景，"
                    "务必看 preview 再决定用不用" % same_as_bg)
    if outside_pct >= 25 and verdict == "ok":
        warn.append("掩膜有 %.0f%% 落在主体高对比区凸包外，多半把背景或投影算进了主体，"
                    "务必看 preview" % outside_pct)

    meta = {
        "source": a.image,
        "verdict": verdict,
        "size": [int(wb.shape[1]), int(wb.shape[0])],
        "orig_size": [w0, h0],
        "crop_box": box,
        "foreground_pct": round(fg_pct, 2),
        "edge_touch": round(touch, 4),
        "fg_near_bg_pct": round(same_as_bg, 1),
        "bright_pct": round(bright_pct, 1),
        "outside_bright_hull_pct": round(outside_pct, 1),
        "border_color_bgr": [int(v) for v in bg_color],
        "warnings": warn,
        "outputs": {"cut": cut_path, "white": white_path, "preview": preview_path},
    }
    with open(a.out + ".json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""干净线稿 → 结构分区 + 印花图层 + 编号 + SVG/JSON。

输入是一张线稿（白底黑线、闭合、等宽），不是照片。
算法：二值化 → 白区连通域 → 按面积分类 → 矢量化 → 拆出印花图层。
"""
import argparse
import json
import os

import cv2
import numpy as np


def load_lineart(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit("读不了图：%s" % path)
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * alpha + 255 * (1 - alpha)).astype(np.uint8)
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    return img if img.ndim == 3 else cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), gray


def vectorize(mask, eps_ratio=0.004, min_area=80):
    """掩膜 → (path_d, 子轮廓数)。孔洞交给 fill-rule:evenodd。"""
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return None, 0
    hier = hier[0]
    subs = []
    for i, c in enumerate(contours):
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        eps = max(1.0, eps_ratio * cv2.arcLength(c, True))
        ap = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(ap) < 3:
            continue
        pts = " L ".join("%d,%.1f" % (int(p[0]), float(p[1])) for p in ap)
        subs.append("M " + pts + " Z")
    return " ".join(subs), len(subs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lineart", required=True)
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--thr", type=int, default=200, help="墨迹阈值，越大线越粗")
    ap.add_argument("--min-area-pct", type=float, default=0.004, help="结构分区最小面积占比")
    ap.add_argument("--max-regions", type=int, default=30)
    ap.add_argument("--keep", default="", help="人工确认：只保留这些序号（按面积从大到小），如 1,2,3")
    ap.add_argument("--drop", default="", help="人工确认：剔除这些序号，如 6,7")
    ap.add_argument("--eps", type=float, default=0.004, help="轮廓简化比例")
    ap.add_argument("--names", default="", help="逗号分隔的语义名，按面积从大到小对应")
    ap.add_argument("--debug-artifacts", action="store_true", help="生成调试用中间文件（print_layer、numbered_preview、regions.svg）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    bgr, gray = load_lineart(args.lineart)
    H, W = gray.shape
    TOTAL = H * W

    ink = (gray < args.thr).astype(np.uint8)
    free = (ink == 0).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(free, 8)
    border_labels = set(np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])))

    # 先算包体轮廓和包面积，分类按包面积比例过滤
    bg_label = max(border_labels, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    bag_area = TOTAL - stats[bg_label, cv2.CC_STAT_AREA]
    sil_mask = np.where(lab == bg_label, 0, 255).astype(np.uint8)
    sil_d, _ = vectorize(sil_mask, 0.003, min_area=300)

    cand = [i for i in range(1, n) if i not in border_labels]
    struct_ids, deco_ids = [], []
    for i in cand:
        a = stats[i, cv2.CC_STAT_AREA]
        pct = a / bag_area
        if pct < args.min_area_pct:
            deco_ids.append(i)                       # 太小（噪声）；其余全保留，用户在页面自己删
        else:
            struct_ids.append(i)                     # 结构分区（不再猜是不是印花/缝线）

    struct_ids.sort(key=lambda i: stats[i, cv2.CC_STAT_TOP])  # 从上到下编号
    print("候选（按 y 从上到下编号，序号用于 --keep/--drop）:")
    for k, i in enumerate(struct_ids[:14], 1):
        a = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        print("  [{}] area={:5.2f}%  fill={:4.2f}  elong={:4.1f}  bbox={}x{}".format(
            k, a / bag_area * 100, a / max(1, w * h), max(w, h) / max(1, min(w, h)), w, h))
    order = list(range(1, len(struct_ids) + 1))
    if args.keep:
        keep = set(int(x) for x in args.keep.split(",") if x.strip())
        dropped = [struct_ids[k - 1] for k in order if k not in keep]
        struct_ids = [i for k, i in enumerate(struct_ids, 1) if k in keep]
    elif args.drop:
        drop = set(int(x) for x in args.drop.split(",") if x.strip())
        dropped = [i for k, i in enumerate(struct_ids, 1) if k in drop]
        struct_ids = [i for k, i in enumerate(struct_ids, 1) if k not in drop]
    else:
        dropped = struct_ids[args.max_regions:]
        struct_ids = struct_ids[: args.max_regions]
    deco_ids += dropped

    # 记录筛选状态：区分人工筛选和自动截断
    if args.keep or args.drop:
        # 人工筛选：用户主动挑选，不算静默截断
        manual_filtered_count = len(struct_ids) + len(dropped)
        truncated_count = 0
    else:
        # 自动截断：超过上限的部分被静默丢弃
        manual_filtered_count = 0
        truncated_count = len(dropped)

    names = [s.strip() for s in args.names.split(",") if s.strip()]
    regions = []
    struct_union = np.zeros((H, W), np.uint8)
    for k, i in enumerate(struct_ids):
        m = (lab == i).astype(np.uint8) * 255
        struct_union = cv2.bitwise_or(struct_union, m)
        d, nsub = vectorize(m, args.eps)
        if not d:
            continue
        x, y = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        w, h = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        # 编号位置：距离变换找核心区（离边缘≥90%最大值），取最大连通块的质心。
        # U 形/环形会把核心区劈成多块，只取最大块质心，避免两块相加掉进中间的缺口。
        # 只在 bbox 内算距离变换，避免整图重复计算
        roi = m[y:y+h, x:x+w]
        dist_roi = cv2.distanceTransform(roi, cv2.DIST_L2, 5)
        _, dmax, _, _ = cv2.minMaxLoc(dist_roi)
        core_roi = (dist_roi >= dmax * 0.9).astype(np.uint8)
        nc, labc, stc, centc = cv2.connectedComponentsWithStats(core_roi, 8)
        if nc > 1:
            big = 1 + int(np.argmax(stc[1:, cv2.CC_STAT_AREA]))
            lx, ly = int(x + centc[big][0]), int(y + centc[big][1])
        else:
            lx, ly = int(x + w / 2), int(y + h / 2)
        regions.append({
            "id": len(regions) + 1,
            "name": names[k] if k < len(names) else "区域%d" % (len(regions) + 1),
            "area_pct": round(float(stats[i, cv2.CC_STAT_AREA]) / bag_area * 100, 1),
            "bbox": [x, y, w, h],
            "cx": lx,
            "cy": ly,
            "d": d,
            "subpaths": nsub,
            "mask_id": int(i),
        })

    lineart_out = np.full((H, W, 3), 255, np.uint8)
    lineart_out[ink == 1] = (20, 20, 20)
    cv2.imwrite(os.path.join(args.out, "lineart.png"), lineart_out)

    # 全墨迹层：alpha = 墨迹浓度，白底透明，供页面按任意颜色叠加
    ink_rgba = np.zeros((H, W, 4), np.uint8)
    ink_rgba[:, :, 3] = (255 - gray).astype(np.uint8)
    cv2.imwrite(os.path.join(args.out, "ink_layer.png"), ink_rgba)

    # 调试用中间文件：默认不生成
    if args.debug_artifacts:
        # ---- 印花图层：去掉结构分区的边界带，剩下的墨迹就是"印在布上的图案" ----
        band = cv2.morphologyEx(struct_union, cv2.MORPH_GRADIENT, np.ones((9, 9), np.uint8))
        for r in regions:  # 区域之间的内边界也要从印花层剔除
            band = cv2.bitwise_or(band, cv2.morphologyEx((lab == r["mask_id"]).astype(np.uint8) * 255,
                                                         cv2.MORPH_GRADIENT, np.ones((7, 7), np.uint8)))
        edges = cv2.dilate(band, np.ones((3, 3), np.uint8))
        print_mask = np.where((ink == 1) & (edges == 0), 255, 0).astype(np.uint8)
        prgba = np.zeros((H, W, 4), np.uint8)
        prgba[:, :, 3] = print_mask
        cv2.imwrite(os.path.join(args.out, "print_layer.png"), prgba)

        # ---- 编号预览（自检用） ----
        pal = [(178, 214, 255), (186, 236, 196), (255, 214, 160), (226, 204, 255),
               (255, 190, 190), (176, 236, 236), (232, 232, 232), (255, 226, 178),
               (200, 224, 255), (220, 248, 196)]
        prev = np.full((H, W, 3), 255, np.uint8)
        for k, r in enumerate(regions):
            m = (lab == r["mask_id"])
            prev[m] = np.array(pal[k % len(pal)], np.uint8)
        prev[ink == 1] = (30, 30, 30)
        for r in regions:
            txt = str(r["id"])
            fs = 1.1 if r["area_pct"] > 3 else 0.8
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
            org = (int(r["cx"] - tw / 2), int(r["cy"] + th / 2))
            cv2.putText(prev, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 6, cv2.LINE_AA)
            cv2.putText(prev, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (25, 25, 25), 2, cv2.LINE_AA)
        cv2.imwrite(os.path.join(args.out, "numbered_preview.png"), prev)

        # ---- SVG ----
        svg_parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H)]
        svg_parts.append('<g id="regions" fill="none" stroke="#1b1a17" stroke-width="4" stroke-linejoin="round">')
        for r in regions:
            svg_parts.append('<path id="r%d" data-name="%s" data-area-pct="%.1f" fill="#ffffff" '
                             'fill-rule="evenodd" d="%s"/>' % (r["id"], escape(r["name"]), r["area_pct"], r["d"]))
        svg_parts.append('</g></svg>')
        with open(os.path.join(args.out, "regions.svg"), "w") as f:
            f.write("\n".join(svg_parts))

    print_pct = 0.0
    try:
        print_pct = round(float((print_mask > 0).mean()) * 100, 2)
    except NameError:
        pass
    meta = {
        "image": {"w": W, "h": H},
        "lineart": "lineart.png",
        "print_layer": "print_layer.png",
        "ink_layer": "ink_layer.png",
        "regions": regions,
        "silhouette": sil_d,
        "deco_count": len(deco_ids),
        "stats": {
            "ink_pct": round(float(ink.mean()) * 100, 2),
            "struct_pct": round(float((struct_union > 0).mean()) * 100, 2),
            "print_pct": print_pct,
        },
        "qc": {
            "bg_pct": round(float(stats[bg_label, cv2.CC_STAT_AREA]) / TOTAL * 100, 1),
            "border_touch": sum(1 for r in regions
                                if r["bbox"][0] <= 1 or r["bbox"][1] <= 1
                                or r["bbox"][0] + r["bbox"][2] >= W - 1
                                or r["bbox"][1] + r["bbox"][3] >= H - 1),
            "regions": len(regions),
            "struct_candidate_count": len(struct_ids) + len(dropped),
            "manual_filtered_count": manual_filtered_count,
            "truncated_count": truncated_count,
            "cand_count": len(cand),
            "max_area_pct": max([r["area_pct"] for r in regions] or [0]),
            "min_area_pct": min([r["area_pct"] for r in regions] or [0]),
            "silhouette_len": len(sil_d),
        },
    }
    with open(os.path.join(args.out, "regions.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(json.dumps({"regions": len(regions), "deco": len(deco_ids),
                      "ink_pct": meta["stats"]["ink_pct"],
                      "print_pct": meta["stats"]["print_pct"]}, ensure_ascii=False))
    for r in regions:
        print("  #{} {:8s} area={:5.1f}% bbox={}".format(
            r["id"], r["name"], r["area_pct"], r["bbox"]))


def escape(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


if __name__ == "__main__":
    main()

"""线稿体检：在"建页之前"5 秒内判断这张线稿能不能用，省掉"先建页再回头重生成"。

为什么要有这一步：一张线稿生成要 30–60 秒，而它能不能切出结构分区，用连通域 3 秒就能
算出来。原来是把稿子跑完整套流程（预处理 → 分区 → 建页）才发现"只有 2 块"，等于把
60 秒的等待押在一次没有验证的出图上。体检只回答一个问题：这张稿值得往下走吗。

用法：
  python3 preflight.py --image lineart_gen.png
  python3 preflight.py --image lineart_gen.png --json          # 只输出机器可读
  python3 preflight.py --image lineart_gen.png --keep-file     # 顺便落一份 lineart.png

输出：先一行中文结论（给人看），再一段 JSON（给流程用）。
退出码：0 = 可以往下走；1 = 建议重生成（附缺什么）；2 = 读图失败；3 = 线稿硬失败，禁止降级交付。
"""
import argparse
import json
import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lineart_prep  # noqa: E402  复用同一套 clean/analyze，别另写一遍阈值

# 与 regions_extract.py 保持同一套判据，否则体检说"能切 6 块"、实际只切出 2 块
MIN_AREA_PCT = 0.004


def classify(stats, total):
    """把连通块按 regions_extract 的规则分类，返回 (struct, deco) 两个列表。
    只按最小面积过滤，不做语义/几何启发式过滤（提示词已约束不要印花/缝线）。"""
    struct, deco = [], []
    for i in range(1, len(stats)):
        a = int(stats[i, cv2.CC_STAT_AREA])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        pct = a / total
        rec = {"area_pct": round(pct * 100, 2),
               "bbox": [int(stats[i, cv2.CC_STAT_LEFT]),
                        int(stats[i, cv2.CC_STAT_TOP]), w, h]}
        if pct < MIN_AREA_PCT:
            deco.append(rec)                       # 太小：碎点
        else:
            struct.append(rec)                     # 结构分区（全部保留）
    struct.sort(key=lambda r: -r["area_pct"])
    return struct, deco



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="刚生成出来的线稿")
    ap.add_argument("--keep-file", default=None, help="把预处理后的干净线稿写到这个路径")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--seal", type=int, default=1)
    args = ap.parse_args()

    if not os.path.exists(args.image):
        print(json.dumps({"ok": False, "error": "读不到文件：%s" % args.image}, ensure_ascii=False))
        return 2

    out = args.keep_file or os.path.join(tempfile.mkdtemp(), "lineart.png")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    ns = argparse.Namespace(image=args.image, out=out, seal=args.seal, despeckle=24, max_side=2048)
    base, thr = lineart_prep.clean(ns)

    img = cv2.imread(out, cv2.IMREAD_GRAYSCALE)
    H, W = img.shape
    TOTAL = H * W
    ink = (img < 200).astype(np.uint8)
    free = (ink == 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, 8)
    border = set(np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])))
    # 背景 = 触边的最大连通块；其余触边块是"漏出去的"前景，不能算候选
    bg = max(border, key=lambda i: stats[i, cv2.CC_STAT_AREA]) if border else 0
    bag_area = TOTAL - stats[bg, cv2.CC_STAT_AREA]
    keep_stats = stats.copy()
    for i in border:                        # 触边的一律不是"包体内部的分区"：背景本身也排除
        keep_stats[i, cv2.CC_STAT_AREA] = 0
    struct, deco = classify(keep_stats, bag_area)

    warnings, hints = [], []
    hard_fail = False

    if base["bg_pct"] < 10.0:
        warnings.append("背景只占 %.1f%%：主体贴边或线稿没留白，分区会外溢" % base["bg_pct"])
        hints.append("重生成时写「只画包体、四周留白、不要背景物件」")
        hard_fail = True
    elif base["bg_pct"] < 20.0:
        warnings.append("背景占 %.1f%%：可能贴边，注意分区外溢" % base["bg_pct"])

    if base["ink_pct"] > 20.0:
        warnings.append("墨迹 %.1f%%：线太粗，分区会被吃掉" % base["ink_pct"])
        hints.append("重生成时写「线条细而均匀等宽、不要涂黑」")
        hard_fail = True
    elif base["ink_pct"] > 15.0:
        warnings.append("墨迹 %.1f%%：线偏粗" % base["ink_pct"])

    if struct and struct[0]["area_pct"] > 35.0:
        warnings.append("最大块 %.0f%%：包身内部几乎没有分割线，最多只能当一整块上色" % struct[0]["area_pct"])
        hints.append("重生成时把「内部结构分区」逐条列出来（袋口翻边 / 袋底折边 / 侧幅 / 提手）")

    if len(struct) < 2:
        warnings.append("分区数太少（%d 块）：线稿可能断线，只能当一整块" % len(struct))
        hints.append("重生成时写「线条干净闭合、无断线」")
        hard_fail = True
    elif len(struct) < 3:
        warnings.append("分区数偏少（%d 块）：简单包型正常，否则可能断线" % len(struct))

    # 不做语义判断（不提手警告、不检测嵌套背景洞），全部交给用户在交互页里决定
    # hard_fail = 硬失败，不能降级交付；leak_suspect = 建议重生成，但可以降级
    if hard_fail:
        verdict = "blocked"
    elif base["leak_suspect"]:
        verdict = "regenerate"
    else:
        verdict = "pass"
    result = {
        "ok": verdict == "pass",
        "verdict": verdict,
        "regions_if_kept_all": len(struct),
        "max_region_pct": struct[0]["area_pct"] if struct else 0,
        "ink_pct": base["ink_pct"],
        "bg_pct": base["bg_pct"],
        "closed_regions": base["closed_regions"],
        "struct": struct[:8],
        "deco": len(deco),
        "warnings": warnings,
        "hints": hints,
        "lineart": out if args.keep_file else None,
    }

    if not args.json:
        if verdict == "pass":
            line = "可往下走：能切出 %d 块，最大块 %.1f%%，墨迹 %.2f%%" % (
                len(struct), result["max_region_pct"], result["ink_pct"])
        elif verdict == "blocked":
            line = "线稿不可用，必须重生成：能切出 %d 块" % len(struct)
        else:
            line = "建议重生成：能切出 %d 块" % len(struct)
        print(line)
        for w in warnings:
            print("  注意：" + w)
        for h in hints:
            print("  改法：" + h)
    print(json.dumps(result, ensure_ascii=False))
    if verdict == "pass":
        return 0
    elif verdict == "blocked":
        return 3
    else:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

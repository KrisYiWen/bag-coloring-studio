"""质检断言：分区结果能不能交货。

失败时退出码 1，并打印原因。只做判定，不改产物。

判定原则：
- 只卡确定性故障（贴边、缺轮廓、分区少于 2 块）
- 不卡"可能有问题"的模糊信号（最大块小）——这些是警告不是门槛
"""
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", required=True)
    ap.add_argument("--max-regions", type=int, default=30)
    a = ap.parse_args()

    meta = json.load(open(a.regions))
    qc = meta.get("qc", {})
    stats = meta.get("stats", {})
    regions = meta.get("regions", [])
    problems = []

    if len(regions) < 2:
        problems.append("只有 %d 块结构分区：至少需要 2 块才能敷色" % len(regions))

    # --- 分区数量上限（超过 30 说明印花被当结构件）---
    # 只检查自动截断，人工筛选不算静默截断
    truncated_count = qc.get("truncated_count", 0)
    if truncated_count > 0:
        struct_cand = qc.get("struct_candidate_count", 0)
        problems.append("自动截断：原始结构候选 %d 块，截断到 %d 块，有 %d 块被静默丢弃，需要人工筛" % (
            struct_cand, a.max_regions, truncated_count))

    # --- 诊断信息（不报警，只输出）---
    bag_pct = stats.get("struct_pct", 0)
    max_pct = qc.get("max_area_pct", 0)
    if max_pct == 0:
        problems.append("最大分区面积为 0：没有可用分区")

    # --- 确定性检查 ---
    if qc.get("border_touch", 0) > 0:
        problems.append("有 {} 块分区贴到画面边缘：线稿没切干净，填充会外溢".format(qc["border_touch"]))
    if not meta.get("silhouette"):
        problems.append("缺少包体轮廓：无法生成分区路径")

    # 输出诊断信息
    bag_pct_val = stats.get("struct_pct", 0)

    out = {"ok": not problems, "checks": {
        "regions": qc.get("regions"),
        "cand_count": qc.get("cand_count"),
        "bag_area_pct": bag_pct_val,
        "max_area_pct": max_pct,
    }, "problems": problems}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if problems:
        sys.stderr.write("QC 未通过：\n".join(" - " + p for p in problems) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

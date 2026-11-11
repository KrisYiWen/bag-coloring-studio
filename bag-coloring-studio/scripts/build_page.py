"""分区 JSON + 线稿 → 单文件可交互 HTML（Canvas 渲染，离线可用）。

页面能力：先选分区再点颜色/面料、上传面料并框选布样、每区独立缩放/角度/平移、
印花层可调、光源方向与强度可调、一键重置回初始帆布、50 步撤销、导出成品 PNG。

--fabric 可选：传一块用户的初始布样，会自动进面料架；不传则不内置任何布样。
重置按钮的行为是把所有分区刷回白底，不依赖任何系统布样。
"""
import argparse
import base64
import json
import re
import os


def html_escape(s):
    """转义 HTML 特殊字符，防止注入"""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))


def default_fabric_jpeg(size=480):
    """画一块米白帆布：两向正弦经纬纹 + 细微噪点，JPEG 质量 88。返回 base64。"""
    import io
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(11)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    warp = np.sin(x * 2 * np.pi / 4.0) * 0.5 + np.sin(y * 2 * np.pi / 4.0) * 0.5
    fine = np.sin((x + y) * 2 * np.pi / 7.0) * 0.3
    duty = (x % 4 < 1).astype(np.float32) * 0.6 + (y % 4 < 1).astype(np.float32) * 0.6 - 0.6
    tex = warp * 4.0 + fine * 3.0 + duty * 3.0 + rng.normal(0, 2.4, (size, size))
    base = np.array([230, 224, 212], np.float32)
    img = np.clip(base[None, None, :] + tex[:, :, None], 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img, "RGB").save(buf, "JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def b64(path, mime):
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


def _subpaths(d):
    parts = [x for x in d.split("M") if x.strip()]
    return ["M" + x for x in parts]


def _bbox(d):
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", d)]
    xs, ys = nums[0::2], nums[1::2]
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def outer_outline(d):
    """保留大子路径（外轮廓 + 大孔洞），丢掉小于外框面积 3% 的碎小孔，
    这样虚线描边不会在印花位置画出一圈圈小虚线。"""
    subs = _subpaths(d)
    if len(subs) <= 1:
        return d
    boxes = [(_bbox(x), x) for x in subs]
    boxes = [(b, x) for b, x in boxes if b is not None]
    if not boxes:
        return d
    big = max(b[2] * b[3] for b, _ in boxes) or 1.0
    keep = [x for b, x in boxes if b[2] * b[3] >= big * 0.03]
    return "".join(keep)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", required=True, help="regions_extract.py 产出的 regions.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="包袋分区填色")
    ap.add_argument("--note", default="")
    ap.add_argument("--fabric", default="",
                    help="可选：用户的初始布样，会自动进面料架；不传则不内置任何布样")
    args = ap.parse_args()

    meta = json.load(open(args.regions))
    for r in meta["regions"]:
        r["outline"] = outer_outline(r["d"])
    base = os.path.dirname(os.path.abspath(args.regions))
    lineart_b64 = b64(os.path.join(base, meta["lineart"]), "image/png")
    print_b64 = b64(os.path.join(base, meta["ink_layer"]), "image/png")
    if args.fabric:
        fabric_b64 = b64(args.fabric, "image/jpeg")
        fabric_src = "user"
    else:
        fabric_b64 = ""
        fabric_src = "none"

    html = TEMPLATE
    html = html.replace("__TITLE__", html_escape(args.title))
    html = html.replace("__NOTE__", html_escape(args.note or "点分区 → 选面料 → 调光 → 导出"))
    html = html.replace("__W__", str(meta["image"]["w"]))
    html = html.replace("__H__", str(meta["image"]["h"]))
    # 安全序列化 JSON，防止 </script> 注入
    regions_json = json.dumps(meta["regions"], ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    outlines_json = json.dumps(
        {str(r["id"]): r.get("outline") or r["d"] for r in meta["regions"]}, ensure_ascii=False
    ).replace("<", "\\u003c").replace(">", "\\u003e")
    html = html.replace("__REGIONS__", regions_json)
    html = html.replace("__OUTLINES__", outlines_json)
    html = html.replace("__SIL__", meta.get("silhouette", ""))
    html = html.replace("__LINEART__", lineart_b64)
    html = html.replace("__PRINT__", print_b64)
    html = html.replace("__FABRIC__", fabric_b64)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(html)
    size = os.path.getsize(args.out)
    print(json.dumps({"out": args.out, "kb": round(size / 1024),
                      "fabric": fabric_src}, ensure_ascii=False))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--ink:#1b1a17;--bg:#f4f1ec;--line:#ded7cc;--accent:#8a6a4f}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  display:flex;flex-direction:column;height:100dvh;overflow:hidden}
header{padding:10px 14px 6px;flex:0 0 auto}
h1{margin:0;font-size:15px;font-weight:600}
.sub{color:#7a7268;font-size:11.5px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* 画布区：占满剩余高度，不滚动 */
.stage{position:relative;flex:1 1 auto;min-height:0;display:grid;place-items:center;padding:8px 10px;overflow:hidden}
.undo,.reset,.number{position:absolute;right:6px;top:6px;z-index:6;width:46px;height:54px;border-radius:12px;
  background:rgba(255,255,255,.94);border:1px solid #e3dbcd;color:#6b4a34;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
  cursor:pointer;box-shadow:0 2px 8px rgba(90,70,50,.09);transition:opacity .15s,transform .1s}
.undo svg,.reset svg,.number svg{width:19px;height:19px;display:block}
.reset{right:auto;left:6px}
.number{right:auto;left:6px;top:auto;bottom:6px}
.undo span,.reset span,.number span{font-size:11px;line-height:1;letter-spacing:1px;color:#8a7259;white-space:nowrap}
.undo:active,.reset:active,.number:active{transform:scale(.95);background:#faf6ef}
.undo[disabled]{opacity:.30;box-shadow:none;pointer-events:none}
.reset{border-color:#d9e6dc;color:#3f6b52}
.reset span{color:#5f7f6b}
.number{border-color:#d9c9a8;color:#8a6a3f}
.number span{color:#8a6a3f}
.number.on{background:#fbf3e2;border-color:#c9a24a;color:#7a5a20}
.number.on span{color:#7a5a20}
canvas#view{display:block;width:100%;height:auto;border-radius:8px;background:#fff;
  box-shadow:0 1px 3px rgba(0,0,0,.08);touch-action:manipulation}
canvas#ov{position:absolute;display:block;border-radius:8px;pointer-events:none;z-index:5}

/* 底部抽屉：固定高度 + 内部滚动，整个页面不滚 */
.sheet{flex:0 0 var(--sheet-h,340px);background:#fff;border-top:1px solid var(--line);
  display:flex;flex-direction:column;
  height:var(--sheet-h,340px);min-height:var(--sheet-h,340px);max-height:var(--sheet-h,340px);
  padding-bottom:env(safe-area-inset-bottom)}
.tabs{display:flex;flex:0 0 auto;gap:2px;padding:6px 8px 0;overflow-x:auto;border-bottom:1px solid #f0ebe3}
.tabs button{border:0;background:transparent;padding:9px 12px;font-size:13.5px;color:#7d7469;border-radius:8px 8px 0 0;white-space:nowrap}
.tabs button.on{color:var(--ink);font-weight:600;background:#faf7f1}
.pane{flex:1 1 auto;min-height:0;overflow:auto;-webkit-overflow-scrolling:touch;padding:10px 12px 60px;display:none}
.pane.on{display:block}

.regions{display:flex;flex-direction:column;margin-top:14px}
.regions .reg + .reg{margin-top:6px}
.reg{display:flex;align-items:center;padding:9px 10px;border-radius:9px;font-size:14px;background:#faf8f4}
.reg.on{background:#f0e4d4;box-shadow:inset 0 0 0 1px #dcc4a8}
.num{width:24px;height:24px;border-radius:50%;background:#eee7db;display:grid;place-items:center;font-size:12px;font-weight:700;flex:0 0 24px}
.reg .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center}
.reg .nm > * + *{margin-left:14px}
.reg .sw{width:18px;height:18px;border-radius:5px;border:1px solid rgba(0,0,0,.12);flex:0 0 18px;background-size:cover;background-position:center}
.reg .pct{color:#948b7c;font-size:11.5px}
.reg.del{opacity:.5;background:#f3f1ec}
.reg.del .num{background:#ddd6cb}
.reg .mini{min-height:30px;padding:4px 10px;font-size:12.5px;border-radius:8px;flex:0 0 auto}
.reg .mini.danger{color:#b4453a;border-color:#e3c2be;background:#fbf3f2}

.row{display:flex;align-items:center;gap:10px;margin:9px 0}
.row label{font-size:13px;color:#6b6358;min-width:52px;flex:0 0 auto}
input[type=range]{flex:1;accent-color:var(--accent);height:34px}
input[type=checkbox]{width:20px;height:20px;accent-color:var(--accent)}
select{font-size:14px;padding:7px 9px;border-radius:9px;border:1px solid var(--line);background:#fff;color:var(--ink)}
button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:8px 12px;font-size:13.5px;color:var(--ink);
  min-height:36px;cursor:pointer}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
button.ghost{background:#faf8f5}
button:active{transform:scale(.985)}
.tip{font-size:11.5px;color:#8c8378;line-height:1.55;margin-top:6px}
/* 面料架：横向格子 + 末尾「＋」，跟色板一个交互隐喻 */
.fabhead{display:flex;align-items:baseline;gap:8px;margin:14px 0 6px}
.fabhead .lb{font-size:12px;color:#8c8378}
.fabrow{display:flex;gap:8px;overflow-x:auto;padding-bottom:2px;-webkit-overflow-scrolling:touch}
.fabcell{position:relative;width:52px;height:52px;flex:0 0 52px;border-radius:10px;overflow:hidden;
  border:1px solid var(--line);background:repeating-conic-gradient(#eee 0 25%,#fff 0 50%) 0 0/10px 10px;cursor:pointer}
.fabcell img{width:100%;height:100%;object-fit:cover;display:block}
.fabcell.on{box-shadow:0 0 0 2px #fff,0 0 0 4px var(--accent)}
.fabcell .del{position:absolute;right:-1px;top:-1px;width:18px;height:18px;border-radius:0 9px 0 8px;
  background:rgba(30,26,22,.72);color:#fff;font-size:11px;line-height:18px;text-align:center;font-weight:700}
.fabcell.add{border:1.5px dashed #c3b6a4;background:#fbf9f5;display:grid;place-items:center;
  font-size:20px;color:#8a6a4f;font-weight:600}
.exports{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.swatches{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
.sw{height:44px;border-radius:10px;border:1px solid rgba(0,0,0,.07);cursor:pointer;position:relative;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.7)}
.sw:active{transform:scale(.96)}
.sw.on{box-shadow:0 0 0 2px #fff,0 0 0 4px var(--accent)}
.sw .del{position:absolute;right:-3px;top:-3px;width:20px;height:20px;border-radius:10px;
  background:#fff;color:#8a6a4f;font:600 13px/20px -apple-system,sans-serif;text-align:center;
  box-shadow:0 1px 3px rgba(0,0,0,.22);cursor:pointer}
.sw .del:active{background:#f3ece2}
.sw.more{background:#fbf9f5;border:1.5px dashed #c3b6a4;display:grid;place-items:center;font-size:20px;color:#8a6a4f;font-weight:600}
.sw.more:active{background:#f4eee4}
.hexrow{display:flex;gap:8px;align-items:center;margin-top:8px}
.hexin{flex:1;min-width:0;padding:11px 12px;border:1px solid var(--line);border-radius:10px;font-size:15px;
  font-family:ui-monospace,Menlo,monospace;text-transform:lowercase}
#picker{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
/* 页面内取色器 */
.colorpicker{display:none;padding:10px 0;border-top:1px solid #eee7db;margin-top:8px}
.colorpicker.on{display:block}
.colorpicker .row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.colorpicker label{font-size:12px;color:#8c8378;min-width:40px}
.colorpicker input[type=range]{flex:1}
.colorpicker .preview{height:36px;border-radius:8px;border:1px solid #eee7db;margin-bottom:8px}
.colorpicker .btns{display:flex;gap:8px}
.colorpicker .btns button{flex:1;height:36px;border-radius:8px;font-size:13px;cursor:pointer}

#modal{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(20,18,16,.66);display:none;place-items:center;z-index:9;padding:12px}
/* 出图层：手机豆包等 WebView 会吞掉 a[download]，改成先看图、长按保存 */
#dl{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(20,18,16,.66);display:none;place-items:center;z-index:12;padding:14px}
#dl.on{display:grid}
#dl .box{background:#fff;border-radius:14px;padding:12px 12px 10px;width:min(420px,94vw);max-height:94dvh;
  display:flex;flex-direction:column;gap:8px;box-shadow:0 12px 34px rgba(40,32,24,.24)}
#dl h3{margin:0;font-size:14px;color:#3b2f26}
#dl img{display:block;width:100%;max-height:62dvh;object-fit:contain;border-radius:8px;background:#fff;
  border:1px solid #eee7db;touch-action:manipulation;-webkit-touch-callout:default}
#dl .tip{font-size:12px;line-height:1.6;color:#8a7259}
#dl .btns{display:flex;gap:8px}
#dl .btns button{flex:1;height:40px;border-radius:10px;font-size:13px;white-space:nowrap;cursor:pointer;
  border:1px solid #e3dbcd;background:#fff;color:#5b4632}
#dl .btns .primary{background:var(--accent);border-color:var(--accent);color:#fff}
#modal.on{display:grid}
.mbox{background:#fff;border-radius:14px;padding:12px;width:min(560px,94vw);max-height:94dvh;display:flex;flex-direction:column}
.mbox h3{margin:0 0 9px;font-size:13.5px;font-weight:600}
canvas#crop{display:block;margin:0 auto;border-radius:8px;touch-action:none}
.mfoot{display:flex;justify-content:space-between;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap}

/* 全屏预览遮罩 */
#preview{position:fixed;top:0;right:0;bottom:0;left:0;background:radial-gradient(circle at 50% 50%, #3a3530 0%, #1e1b18 55%, #141210 100%);display:none;z-index:20;overflow:hidden;touch-action:none}
#preview.on{display:block}
#preview canvas{position:absolute;top:50%;left:50%;touch-action:none}
#preview .close{position:absolute;top:14px;right:14px;z-index:21;width:38px;height:38px;border-radius:50%;
  background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);color:#fff;font-size:18px;
  display:grid;place-items:center;cursor:pointer;min-height:0;padding:0}
#preview .hint{position:absolute;bottom:28px;left:0;right:0;text-align:center;color:rgba(255,255,255,.45);font-size:12px;z-index:21;pointer-events:none}

/* 暂存缩略图格子 */
.snaphead{display:flex;align-items:baseline;gap:8px;margin:14px 0 6px}
.snaphead .lb{font-size:12px;color:#8c8378}
.snaprow{display:flex;gap:8px;overflow-x:auto;padding-bottom:2px;-webkit-overflow-scrolling:touch}
.snapcell{position:relative;width:64px;height:64px;flex:0 0 64px;border-radius:10px;overflow:hidden;
  border:1px solid var(--line);background:#faf8f5;cursor:pointer}
.snapcell img{width:100%;height:100%;object-fit:cover;display:block}
.snapcell .del{position:absolute;right:-1px;top:-1px;width:18px;height:18px;border-radius:0 9px 0 8px;
  background:rgba(30,26,22,.72);color:#fff;font-size:11px;line-height:18px;text-align:center;font-weight:700;z-index:2;min-height:0;padding:0}
.snapcell .apply{position:absolute;left:0;right:0;bottom:0;background:rgba(30,26,22,.58);color:#fff;
  font-size:10px;line-height:16px;text-align:center;z-index:2}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__NOTE__</div>
</header>

<div class="stage"><canvas id="view"></canvas><canvas id="ov"></canvas><button class="undo" id="undoBtn" type="button" title="撤销上一步" disabled aria-label="撤销">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/>
        <path d="M3 3.5V8h4.5"/>
      </svg>
      <span>撤销</span>
    </button><button class="reset" id="resetBtn" type="button" title="所有分区刷回白底">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M4.5 10.5a7.8 7.8 0 0 1 13.6-3.9"/><path d="M19.5 4.2v4.3h-4.3"/>
        <path d="M19.5 13.5a7.8 7.8 0 0 1-13.6 3.9"/><path d="M4.5 19.8v-4.3h4.3"/>
      </svg>
      <span>重置</span>
    </button></div>

<div id="dl"><div class="box">
  <h3>成品图已生成</h3>
  <img id="dlImg" alt="成品图">
  <div class="tip" id="dlTip">长按图片可保存到相册</div>
  <div class="btns"><button class="primary" id="dlSave">系统分享</button><button id="dlCopy">复制链接</button><button id="dlClose">关闭</button></div>
</div></div>

<div class="sheet">
  <div class="tabs">
    <button data-pane="paneRegion" class="on">分区</button>
    <button data-pane="paneFab">配色</button>
    <button data-pane="paneInk">线稿</button>
    <button data-pane="paneLight">光照</button>
    <button data-pane="paneOut">保存</button>
  </div>

  <div class="pane on" id="paneRegion">
    <div class="row"><label style="min-width:auto">显示编号</label><input type="checkbox" id="showNum" checked></div>
    <div class="tip">点列表里的分区名可选中（画布上出蓝色虚线）；不需要的分区用「删除」隐藏，画布上那块变白底；删错了可以撤销或恢复。</div>
    <div class="regions" id="regions"></div>
  </div>

  <div class="pane" id="paneFab">
    <div class="row" style="margin:0 0 6px"><label style="min-width:auto;font-size:12px;color:#8c8378">颜色</label>
      <span class="tip" style="margin:0" id="assignHint">颜色/面料先备着，选好分区再点它上色</span></div>
    <div class="swatches" id="swatches"></div>
    <div class="hexrow">
      <input class="hexin" id="hexIn" placeholder="输入色值，如 #cfe9dd" inputmode="latin" autocomplete="off" spellcheck="false">
      <button class="ghost" id="hexApply">用它</button>
    </div>
    <div class="colorpicker" id="colorPicker">
      <div class="preview" id="cpPreview"></div>
      <div class="row"><label>色相</label><input type="range" id="cpHue" min="0" max="360" value="160"></div>
      <div class="row"><label>亮度</label><input type="range" id="cpLight" min="0" max="100" value="70"></div>
      <div class="btns">
        <button class="ghost" id="cpCancel">取消</button>
        <button class="primary" id="cpOk">用这个颜色</button>
      </div>
    </div>
    <div class="fabhead"><span class="lb">面料</span><span class="tip" id="fabTip" style="margin:0">先点分区，再点面料才会贴上去</span></div>
    <div class="fabrow" id="fabRow"></div>
    <div id="texBox" style="display:none">
      <div class="tip" id="texWho" style="margin-top:8px">—</div>
      <div class="row"><label>缩放</label><input type="range" id="pScale" min="20" max="300" value="100"></div>
      <div class="row"><label>旋转</label><input type="range" id="pRot" min="-90" max="90" value="0"></div>
      <div class="row"><label>平移</label><input type="range" id="pOff" min="-200" max="200" value="0"></div>
    </div>
    <div class="tip">每块分区的布料参数各自独立；同一块布贴到不同分区可以调成不同大小与角度。</div>
  </div>

  <div class="pane" id="paneInk">
    <div class="row"><label>显示线条</label><input type="checkbox" id="printOn" checked>
      <span style="flex:1"></span>
      <select id="printColor"><option value="#1a1a1a">黑</option><option value="#ffffff">白</option><option value="#8a6a4f">棕</option></select>
    </div>
    <div class="tip">深色布料上把线稿调成白色更好看。</div>
    <div class="row"><label>浓度</label><input type="range" id="printOp" min="0" max="100" value="92"></div>
    <div class="row"><label style="min-width:auto">加粗轮廓</label><input type="checkbox" id="outlineOn"></div>
  </div>

  <div class="pane" id="paneLight">
    <div class="row"><label>方向</label><input type="range" id="lightAng" min="0" max="360" value="315"></div>
    <div class="row"><label>强度</label><input type="range" id="lightInt" min="0" max="100" value="55"></div>
    <div class="tip">线稿没有真实褶皱，明暗是按分区分配合成的；拖动方向可看不同侧光。</div>
  </div>

  <div class="pane" id="paneOut">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <button id="btnPreview">全屏预览</button>
      <button id="btnSnapshot">暂存配色</button>
    </div>
    <button class="primary" id="expPng" style="width:100%;margin-top:8px">保存到本地</button>
    <div class="snaphead" id="snapHead" style="display:none"><span class="lb">已暂存方案（点「应用」还原）</span></div>
    <div class="snaprow" id="snapRow"></div>
    <div class="tip">预览可双指缩放查看；暂存会记下当前配色，最多存 10 个方案。</div>
  </div>
</div>

<div id="modal"><div class="mbox">
  <h3>框选一块作为布样（尽量选平整、无褶皱的区域）</h3>
  <canvas id="crop"></canvas>
  <div class="mfoot">
    <span class="tip" id="cropTip">空白处拖出新框 · 框内可拖动 · 拖四角改大小</span>
    <span><button class="ghost" id="cropCancel">取消</button>
    <button class="ghost" id="cropReset">重选</button>
    <button class="primary" id="cropOk">用这块</button></span>
  </div>
</div></div>

<div id="preview"><canvas id="prevCanvas"></canvas><button class="close" id="prevClose">×</button><div class="hint">双指缩放 · 双击复位</div></div>

<script>
const META = __REGIONS__;
const SIL = "__SIL__";
const LINEART_SRC = "__LINEART__";
const PRINT_SRC = "__PRINT__";
const PRESET_FABRIC = "__FABRIC__";

const W = __W__, H = __H__;
const view = document.getElementById('view');
view.width = W; view.height = H;
const ctx = view.getContext('2d');
const ovC = document.getElementById('ov');
const ovx = ovC.getContext('2d');

const state = {
  selected: null,
  fabrics: [],           // [{id, name, tile(无缝 canvas), raw(原布样 dataURL), tex:{scale,rotate,offset}}]
  activeFabricId: null,  // 手上这块布：只在"选中分区 + 点它"时贴上
  regionFabric: {},      // id -> {fabricId, tex:{...}} 或 {solid:'#xxx', fabricId, tex}
  light: {deg: 315, inten: 0.55},
  print: {on: true, color: '#1a1a1a', op: 0.92},
  outline: false,
  showNum: true,
  custom: [],            // 用户自己加的颜色（最多 5 个）
  palette: ['#cfe9dd','#c9e3f2','#f7dde2','#f7efcf','#e0daf3','#f7e6d3',
            '#e9f1dd','#dff0ef','#f4e8ef','#efece6'],
  hidden: {},            // id -> true：用户删除的分区（画布上变白底，不渲染、不编号、不可选中）
  snapshots: [],         // [{thumb: dataURL, state: {regionFabric, hidden, activeFabricId}}]
};
const FAB_MAX = 8;       // 面料架上限，超出丢最早

const paths = {};
META.forEach(r => { paths[r.id] = new Path2D(r.d); });
const silPath = new Path2D(SIL);
const OUTLINES = __OUTLINES__;
const outPaths = {};
META.forEach(r => { outPaths[r.id] = new Path2D(OUTLINES[r.id] || r.d); });

/* ---------- 面料架 ---------- */
let fabSeq = 0;

// 四边镜像拼接：消除平铺接缝（布样本身不是无缝的）
function makeSeamless(img){
  const w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
  const c = document.createElement('canvas');
  c.width = w * 2; c.height = h * 2;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0, w, h);
  g.save(); g.translate(w * 2, 0); g.scale(-1, 1); g.drawImage(img, 0, 0, w, h); g.restore();
  g.save(); g.translate(0, h * 2); g.scale(1, -1); g.drawImage(img, 0, 0, w, h); g.restore();
  g.save(); g.translate(w * 2, h * 2); g.scale(-1, -1); g.drawImage(img, 0, 0, w, h); g.restore();
  return c;
}

function fabById(id){
  for (let i = 0; i < state.fabrics.length; i++) if (state.fabrics[i].id === id) return state.fabrics[i];
  return null;
}

// 新布进架：不碰任何分区，只挂到架上并高亮成"手上面料"
function addFabric(img, name, sys){
  const raw = img.src || (img.toDataURL ? img.toDataURL('image/png') : '');
  const tile = makeSeamless(img);
  const fab = {
    id: 'f' + (++fabSeq),
    name: name || ('面料 ' + (state.fabrics.length + 1)),
    tile: tile,
    raw: raw,
    sys: !!sys,                                   // 系统布（初始帆布）：只给"重置"用，不进面料架
    tex: { scale: (W * 0.46) / tile.width, rotate: 0, offset: 0 }
  };
  state.fabrics.push(fab);
  // 系统布不占用户的 8 格名额，也不会被淘汰
  const userCount = () => state.fabrics.filter(f => !f.sys).length;
  while (userCount() > FAB_MAX) {
    const idx = state.fabrics.findIndex(f => !f.sys);
    if (idx < 0) break;
    const dropped = state.fabrics.splice(idx, 1)[0];
    if (state.activeFabricId === dropped.id) state.activeFabricId = null;
    purgeFabric(dropped.id);   // 用到这块布的分区退回色板色，不留悬空引用
  }
  // 系统布不抢"手上面料"
  if (!fab.sys) state.activeFabricId = fab.id;
  buildFabricRow(); syncTexUI(); render();
}

// 一块布没了：把用到它的分区清干净（有纯色就只去掉布，没有就整个退回色板）
function purgeFabric(fabId){
  META.forEach(r => {
    const e = state.regionFabric[r.id];
    if (e && e.fabricId === fabId) {
      if (e.solid) { delete e.fabricId; delete e.tex; }
      else delete state.regionFabric[r.id];
    }
  });
}

function loadFabric(src, name, sys){
  const im = new Image();
  im.onload = () => addFabric(im, name, sys);
  im.src = src;
}

// 贴布：只有"选中分区 + 点面料"才会走到这里
function applyFabric(fab){
  if (!fab) return;
  state.activeFabricId = fab.id;
  if (!state.selected) { buildFabricRow(); syncTexUI(); return; }
  snapshot();
  const prev = state.regionFabric[state.selected];
  // 这块分区以前用过布 → 沿用那套参数；否则用这块布的默认参数
  const tex = (prev && prev.fabricId === fab.id && prev.tex) ? prev.tex
            : { scale: fab.tex.scale, rotate: fab.tex.rotate, offset: fab.tex.offset };
  state.regionFabric[state.selected] = { fabricId: fab.id, tex: tex };
  buildRegionList(); buildFabricRow(); buildSwatches(); syncTexUI(); render();
}

// 滑杆作用于谁：优先选中分区，其次"手上面料"的默认参数；都没有就隐藏
function texTarget(){
  const rf = state.selected ? state.regionFabric[state.selected] : null;
  if (rf && rf.fabricId) {
    const fab = fabById(rf.fabricId);
    // 初始帆布是"一键复原"的结果，不提供缩放/角度调节
    if (fab && !fab.sys) return { fab: fab, tex: rf.tex, who: '分区 #' + state.selected + ' 的布料' };
  }
  if (state.activeFabricId) {
    const fab = fabById(state.activeFabricId);
    if (fab) return { fab: fab, tex: fab.tex, who: fab.name + '（默认参数，之后贴上的分区用这套）' };
  }
  return null;
}

function syncTexUI(){
  const box = document.getElementById('texBox');
  const who = document.getElementById('texWho');
  const t = texTarget();
  if (!box) return;
  if (!t) { box.style.display = 'none'; return; }
  box.style.display = 'block';
  who.textContent = t.who;
  document.getElementById('pScale').value = Math.round(t.tex.scale * 100);
  document.getElementById('pRot').value = Math.round(t.tex.rotate);
  document.getElementById('pOff').value = Math.round(t.tex.offset);
}

function patternFor(tile, tex, ctxTarget){
  const p = ctxTarget.createPattern(tile, 'repeat');
  const m = new DOMMatrix();
  m.translateSelf(tex.offset, tex.offset * 0.6);
  m.rotateSelf(tex.rotate);
  m.scaleSelf(tex.scale, tex.scale);
  if (p.setTransform) p.setTransform(m);
  return p;
}

// 底布用最大分区那套填充（避免未分区处露白）
const BASE_REG = (function(){
  let best = { r: META[0], idx: 0 };
  META.forEach((r, i) => { if (r.area_pct > best.r.area_pct) best = { r: r, idx: i }; });
  return best;
})();

function fillStyleFor(r, idx, target){
  const rf = state.regionFabric[r.id];
  if (rf) {
    if (rf.solid) return rf.solid;
    if (rf.fabricId) {
      const fab = fabById(rf.fabricId);
      if (fab) return patternFor(fab.tile, rf.tex || fab.tex, target);
    }
  }
  return state.palette[idx % state.palette.length];
}

/* ---------- 线稿层 ---------- */
const inkImg = new Image();
let inkReady = false;
inkImg.onload = () => { inkReady = true; render(); };
inkImg.src = PRINT_SRC;
let inkCache = {key: null, cv: null};
function getInk(color){
  const key = color;
  if (inkCache.key === key) return inkCache.cv;
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d');
  g.drawImage(inkImg, 0, 0);
  g.globalCompositeOperation = 'source-in';
  g.fillStyle = color; g.fillRect(0, 0, W, H);
  inkCache = {key, cv: c};
  return c;
}

/* ---------- 渲染 ---------- */
function shadeRegion(g, r, path){
  const a = state.light.deg * Math.PI / 180;
  const I = state.light.inten;
  if (I <= 0.001) return;
  const cx = r.bbox[0] + r.bbox[2] / 2, cy = r.bbox[1] + r.bbox[3] / 2;
  const L = Math.max(r.bbox[2], r.bbox[3]) * 0.62;
  const dx = Math.cos(a), dy = Math.sin(a);
  // 暗部：沿光反方向加深
  const dg = g.createLinearGradient(cx + dx * L, cy + dy * L, cx - dx * L, cy - dy * L);
  dg.addColorStop(0, 'rgba(0,0,0,0)');
  dg.addColorStop(0.55, 'rgba(0,0,0,' + (0.06 * I) + ')');
  dg.addColorStop(1, 'rgba(0,0,0,' + (0.46 * I) + ')');
  g.fillStyle = dg; g.fillRect(r.bbox[0] - 2, r.bbox[1] - 2, r.bbox[2] + 4, r.bbox[3] + 4);
  // 亮部：靠光一侧提亮
  const lg = g.createLinearGradient(cx + dx * L, cy + dy * L, cx - dx * L, cy - dy * L);
  lg.addColorStop(0, 'rgba(255,255,255,' + (0.30 * I) + ')');
  lg.addColorStop(0.5, 'rgba(255,255,255,0)');
  g.save(); g.globalCompositeOperation = 'screen';
  g.fillStyle = lg; g.fillRect(r.bbox[0] - 2, r.bbox[1] - 2, r.bbox[2] + 4, r.bbox[3] + 4);
  g.restore();
}

function render(){
  const lw = Math.max(2, Math.round(W / 480));
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);

  // 填充优先级：本区纯色 > 本区面料（用本区自己的变换参数）> 色板兜底
  // 不再铺"整只包体轮廓"的底布：那个轮廓是"所有非背景像素"，会把提手拱内的背景洞、
  // 分区之间的缝隙一起裹进来。给最大分区敷色时，整只包（含拱内空地）都会跟着变——
  // 正是"一贴布就全场覆盖"的视觉来源。每个分区本来都有自己的兜底色，逐区填就够。

  META.forEach((r, idx) => {
    if (state.hidden[r.id]) return;   // 用户删除的分区：画布上变白底，不参与渲染
    const p = paths[r.id];
    ctx.save();
    ctx.beginPath();
    ctx.clip(p);
    ctx.fillStyle = fillStyleFor(r, idx, ctx);
    ctx.fillRect(0, 0, W, H);
    shadeRegion(ctx, r, p);
    ctx.restore();
  });

  // 线稿层：直接叠原始墨迹，保证与原稿每一条线一致（不做分区描边，避免双线）
  if (inkReady && state.print.on && state.print.op > 0) {
    ctx.save();
    ctx.globalAlpha = state.print.op;
    ctx.drawImage(getInk(state.print.color), 0, 0, W, H);
    ctx.restore();
  }

  // 可选：加粗结构轮廓（默认关闭，只用线稿层）
  if (state.outline) {
    const lw = Math.max(2, Math.round(W / 480));
    ctx.save();
    ctx.strokeStyle = '#1b1a17'; ctx.lineWidth = lw; ctx.lineJoin = 'round';
    ctx.stroke(silPath);
    ctx.restore();
  }

  // 编号
  if (state.showNum) {
    const placed = [];
    META.forEach(r => {
      if (state.hidden[r.id]) return;
      const fs = Math.round(W * (r.area_pct > 3 ? 0.042 : 0.030));
      let x = r.cx, y = r.cy;
      // 编号撞车时依次下移，避免两个小区块的数字叠在一起
      for (let k = 0; k < 6; k++) {
        const hitP = placed.some(p => Math.abs(p[0] - x) < fs * 1.6 && Math.abs(p[1] - y) < fs * 1.5);
        if (!hitP) break;
        y += fs * 1.5;
      }
      placed.push([x, y]);
      ctx.font = '600 ' + fs + 'px -apple-system, "PingFang SC", sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = fs * 0.18; ctx.strokeStyle = 'rgba(255,255,255,.92)';
      ctx.strokeText(r.id, x, y);
      ctx.fillStyle = '#221f1c';
      ctx.fillText(r.id, x, y);
    });
  }
  selAnim();
  placeReset();   // 预置布异步加载完成后按钮才该出现
}
/* ---------- 选中框：overlay 上的流动蚂蚁线 ---------- */
let dashOff = 0, animId = 0, lastTick = 0;
const DASH_ON = 0.022, DASH_OFF = 0.016;   // 与画布宽度成比例，缩放后节奏一致；加粗后段长也略放长

function drawSel(){
  ovx.setTransform(1, 0, 0, 1, 0, 0);
  ovx.clearRect(0, 0, ovC.width, ovC.height);
  if (!state.selected || !outPaths[state.selected]) return;
  const pth = outPaths[state.selected];
  // 蚂蚁线加粗：手机上原 1.5×lw≈3px 偏细，看不清选中了哪块
  const lw = Math.max(3, Math.round(W / 320));
  ovx.save();
  ovx.lineJoin = 'round'; ovx.lineCap = 'round';
  ovx.setLineDash([]);
  ovx.strokeStyle = 'rgba(255,255,255,.42)';          // 淡白垫底：深色布面上也压得住
  ovx.lineWidth = lw * 3.4;
  ovx.stroke(pth);
  ovx.setLineDash([W * DASH_ON, W * DASH_OFF]);
  ovx.lineDashOffset = dashOff;
  ovx.strokeStyle = 'rgba(37,99,235,.92)';            // 半透明蓝
  ovx.lineWidth = lw * 2.2;
  ovx.stroke(pth);
  ovx.restore();
}

function animLoop(t){
  const dt = lastTick ? Math.min(120, t - lastTick) : 33;
  lastTick = t;
  dashOff += W * (DASH_ON + DASH_OFF) * dt / 1000;    // 约每秒走完一个虚线周期
  drawSel();
  animId = requestAnimationFrame(animLoop);
}

function selAnim(){
  drawSel();
  if (state.selected && outPaths[state.selected]) {
    if (!animId) { lastTick = 0; animId = requestAnimationFrame(animLoop); }
  } else if (animId) {
    cancelAnimationFrame(animId); animId = 0;
    ovx.clearRect(0, 0, ovC.width, ovC.height);
  }
}

/* ---------- 点选 ---------- */
function toCanvasXY(e){
  const rect = view.getBoundingClientRect();
  return [ (e.clientX - rect.left) * (W / rect.width), (e.clientY - rect.top) * (H / rect.height) ];
}
// 捕获阶段先判定这次点击是否来自抽屉：色卡/面料格点完会重建 DOM，
// 到冒泡阶段 e.target 已脱离文档，closest('.sheet') 会失效，导致"点个颜色就退出选中"
let clickInSheet = false;
document.addEventListener('click', e => {
  clickInSheet = !!(e.target.closest && (e.target.closest('.sheet') || e.target.closest('#dl') || e.target.closest('#modal')));
}, true);

document.addEventListener('click', e => {
  // 抽屉里的控件不参与"点空白取消选中"
  if (clickInSheet || (e.target.closest && (e.target.closest('.undo') || e.target.closest('.reset') || e.target.closest('.number')))) return;
  if (e.target === view) return;          // 画布内的命中交给下面的处理器
  if (state.selected) { state.selected = null; buildRegionList(); buildSwatches(); syncTexUI(); render(); }
});

view.addEventListener('click', e => {
  const [x, y] = toCanvasXY(e);
  let hit = null;
  // 逆序：后画的在上层；hidden 分区不参与命中（画布上那块=白底，点上去就是取消选中）
  for (let i = META.length - 1; i >= 0; i--) {
    const r = META[i];
    if (state.hidden[r.id]) continue;
    ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
    const inPath = ctx.isPointInPath(paths[r.id], x, y);
    ctx.restore();
    if (inPath) { hit = r.id; break; }
  }
  state.selected = hit;
  // 选中分区只做选中，不自动把"手上面料"贴上去
  buildRegionList(); buildSwatches(); syncTexUI(); render();
});

/* ---------- 分区列表（线稿 tab 内） ---------- */
function buildRegionList(){
  const box = document.getElementById('regions');
  if (!box) return;
  box.innerHTML = '';
  META.forEach((r, idx) => {
    const del = !!state.hidden[r.id];
    const d = document.createElement('div');
    d.className = 'reg' + (del ? ' del' : '') + (state.selected === r.id ? ' on' : '');
    const rf = state.regionFabric[r.id];
    const left = document.createElement('span');
    left.className = 'nm';
    left.innerHTML = '<span class="num">' + r.id + '</span>';
    // 预览小色块/面料缩略图：与画布渲染同套逻辑（solid > fabric > palette 兜底）
    if (!del) {
      const sw = document.createElement('span');
      sw.className = 'sw';
      if (rf && rf.solid) {
        sw.style.background = rf.solid;
      } else if (rf && rf.fabricId) {
        const fb = fabById(rf.fabricId);
        if (fb) sw.style.backgroundImage = 'url(' + fb.raw + ')';
      } else {
        sw.style.background = state.palette[idx % state.palette.length];
      }
      left.appendChild(sw);
    }
    if (del) {
      const s = document.createElement('span');
      s.style.cssText = 'opacity:.5;text-decoration:line-through';
      s.textContent = '已删除';
      left.appendChild(s);
    }
    d.appendChild(left);
    // 行内操作：点行=选中；右边小按钮=删除/恢复（点按钮不触发选中）
    if (del) {
      const b = document.createElement('button');
      b.className = 'mini'; b.textContent = '恢复';
      b.onclick = e => {
        e.stopPropagation();
        snapshot();
        delete state.hidden[r.id];
        buildRegionList(); buildSwatches(); syncTexUI(); render();
      };
      d.appendChild(b);
    } else {
      const b = document.createElement('button');
      b.className = 'mini danger'; b.textContent = '删除';
      b.onclick = e => {
        e.stopPropagation();
        snapshot();
        state.hidden[r.id] = true;
        if (state.selected === r.id) state.selected = null;
        buildRegionList(); buildSwatches(); syncTexUI(); render();
      };
      d.appendChild(b);
    }
    d.onclick = () => { if (!del) { state.selected = r.id; buildRegionList(); buildSwatches(); syncTexUI(); render(); } };
    box.appendChild(d);
  });
  // 选中项自动滚动到视窗中间（画布上点选分区时，列表同步居中）
  const sel = box.querySelector('.reg.on');
  if (sel) {
    const pane = box.closest('.pane');
    if (pane && pane.getBoundingClientRect().height > 0) {
      const pr = pane.getBoundingClientRect();
      const sr = sel.getBoundingClientRect();
      pane.scrollTop += (sr.top - pr.top - (pr.height - sr.height) / 2);
    }
  }
}

/* ---------- 面料上传 + 框选 ---------- */
const modal = document.getElementById('modal');
const crop = document.getElementById('crop');
const cctx = crop.getContext('2d');
let cropImg = null, cropRect = null, dragStart = null, cropScale = 1;
let cropMode = null, cropGrab = null, cropAnchor = null, cropCorner = -1;   // draw / move / resize

function openUpload(){
  const input = document.createElement('input'); input.type = 'file'; input.accept = 'image/*';
  input.onchange = () => {
    const f = input.files[0]; if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      const im = new Image();
      im.onload = () => { cropImg = im; openCrop(); };
      im.src = rd.result;
    };
    rd.readAsDataURL(f);
  };
  input.click();
}

/* ---------- 面料架渲染 ---------- */
function buildFabricRow(){
  const row = document.getElementById('fabRow');
  if (!row) return;
  row.innerHTML = '';
  state.fabrics.forEach(fab => {
    if (fab.sys) return;                       // 初始帆布不在架子上露脸
    const d = document.createElement('div');
    d.className = 'fabcell' + (state.activeFabricId === fab.id ? ' on' : '');
    d.title = fab.name + '（先点分区，再点这块布才会贴上去）';
    const im = new Image();
    im.src = fab.raw;            // 缩略图用原布样，不用镜像拼接图，否则认不出是哪块布
    d.appendChild(im);
    const del = document.createElement('span');
    del.className = 'del'; del.textContent = '×'; del.title = '删掉这块布';
    del.onclick = ev => {
      ev.stopPropagation();
      state.fabrics = state.fabrics.filter(x => x.id !== fab.id);
      if (state.activeFabricId === fab.id) state.activeFabricId = null;
      purgeFabric(fab.id);     // 正在用它的分区退回色板色
      buildFabricRow(); syncTexUI(); buildRegionList(); buildSwatches(); render();
    };
    d.appendChild(del);
    d.onclick = () => applyFabric(fab);
    row.appendChild(d);
  });
  // 末尾「＋」：上传新布
  const add = document.createElement('div');
  add.className = 'fabcell add';
  add.textContent = '＋';
  add.title = '上传我的面料';
  add.onclick = openUpload;
  row.appendChild(add);
}

/* 坐标约定：cropRect 一律存**原图像素坐标**，画布只在绘制时换算。
   以前 cropRect 存画布坐标、drawImage 又把它当源图坐标用，手机大图上取样点
   会偏 3–6 倍，框里显示成照片的暗部（看起来就是一块黑）；现在只维护一套坐标。 */
function openCrop(){
  // 显示尺寸在这里一次算死，CSS 只管圆角和 touch-action。
  // 绝不能写成 width:100% + height:auto + max-height：竖图会被 56dvh 夹住高度、宽度不动，
  // 画面就被压扁（真实 bug：有的布样一打开框选页面就是变形的）。
  const boxW = Math.min(540, window.innerWidth * 0.94) - 26;   // .mbox padding 12*2 + 边框余量
  const boxH = Math.max(220, window.innerHeight * 0.56);
  cropScale = Math.min(boxW / cropImg.width, boxH / cropImg.height, 1);
  crop.style.width  = Math.round(cropImg.width  * cropScale) + 'px';
  crop.style.height = Math.round(cropImg.height * cropScale) + 'px';
  crop.width  = Math.round(cropImg.width  * cropScale * 2);    // 2× 背板，缩放后仍然清晰
  crop.height = Math.round(cropImg.height * cropScale * 2);
  cropRect = null; dragStart = null; cropMode = null; cropGrab = null; cropAnchor = null; cropCorner = -1;
  drawCrop();
  modal.classList.add('on');
}
function cropF(){ return crop.width / cropImg.width; }   // 画布像素 / 原图像素
function drawCrop(){
  if (!cropImg) return;
  const f = cropF();
  cctx.setTransform(1, 0, 0, 1, 0, 0);
  cctx.clearRect(0, 0, crop.width, crop.height);
  cctx.drawImage(cropImg, 0, 0, crop.width, crop.height);
  if (cropRect) {
    const [ix, iy, iw, ih] = cropRect;
    const cx = ix * f, cy = iy * f, cw = iw * f, ch = ih * f;
    cctx.save();
    // 框外压深一点，框内保持原亮度，对比更明确
    cctx.fillStyle = 'rgba(0,0,0,.62)'; cctx.fillRect(0, 0, crop.width, crop.height);
    cctx.clearRect(cx, cy, cw, ch);
    cctx.drawImage(cropImg, ix, iy, iw, ih, cx, cy, cw, ch);
    cctx.strokeStyle = '#fff'; cctx.lineWidth = 2;
    cctx.strokeRect(cx + 1, cy + 1, Math.max(0, cw - 2), Math.max(0, ch - 2));
    // 四角手柄：告诉用户"这个角能拉"，也能拖住整框移动
    const hs = 9;
    [[cx, cy], [cx + cw, cy], [cx + cw, cy + ch], [cx, cy + ch]].forEach(pt => {
      cctx.fillStyle = '#fff';
      cctx.fillRect(pt[0] - hs / 2, pt[1] - hs / 2, hs, hs);
      cctx.strokeStyle = 'rgba(60,45,30,.55)'; cctx.lineWidth = 1;
      cctx.strokeRect(pt[0] - hs / 2, pt[1] - hs / 2, hs, hs);
    });
    cctx.restore();
  }
}
function cropXY(e){
  const rect = crop.getBoundingClientRect();
  if (!rect.width || !rect.height) return [0, 0];
  return [ (e.clientX - rect.left) / rect.width  * cropImg.width,
           (e.clientY - rect.top)  / rect.height * cropImg.height ];
}
// 画布 CSS 像素 / 原图像素：热区按屏幕手感算，坐标仍走原图像素
function cropCssScale(){
  const r = crop.getBoundingClientRect();
  return r.width ? r.width / cropImg.width : (cropScale || 1);
}
function cropMinSide(){ return Math.max(16, Math.round(cropImg.width * 0.03)); }

crop.addEventListener('pointerdown', e => {
  e.preventDefault();
  const p = cropXY(e);
  const hit = 22 / Math.max(cropCssScale(), 1e-6);      // 22 CSS px 的角柄热区
  cropMode = null; cropGrab = null; cropAnchor = null; cropCorner = -1;
  if (cropRect) {
    const [x, y, w, h] = cropRect;
    const corners = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
    for (let i = 0; i < 4; i++) {
      if (Math.hypot(p[0] - corners[i][0], p[1] - corners[i][1]) <= hit) {
        cropMode = 'resize'; cropCorner = i;
        cropAnchor = corners[(i + 2) % 4];               // 对角顶点固定不动
        crop.setPointerCapture(e.pointerId);
        return;
      }
    }
    // 落在框里 → 拖整框去找花纹，不再清空重画
    if (p[0] >= x && p[0] <= x + w && p[1] >= y && p[1] <= y + h) {
      cropMode = 'move'; cropGrab = [p[0] - x, p[1] - y];
      crop.setPointerCapture(e.pointerId);
      return;
    }
  }
  cropMode = 'draw'; dragStart = p; cropRect = null;
  crop.setPointerCapture(e.pointerId);
});

crop.addEventListener('pointermove', e => {
  if (!cropMode) return;
  const p = cropXY(e);
  const W = cropImg.width, H = cropImg.height;
  if (cropMode === 'move') {
    const w = cropRect[2], h = cropRect[3];
    const nx = Math.max(0, Math.min(W - w, p[0] - cropGrab[0]));
    const ny = Math.max(0, Math.min(H - h, p[1] - cropGrab[1]));
    cropRect = [nx, ny, w, h];
  } else if (cropMode === 'resize' && cropAnchor) {
    const a = cropAnchor, minSide = cropMinSide();
    const px = Math.max(0, Math.min(W, p[0]));
    const py = Math.max(0, Math.min(H, p[1]));
    let w = Math.max(minSide, Math.abs(px - a[0]));
    let h = Math.max(minSide, Math.abs(py - a[1]));
    const left = px < a[0] ? a[0] - w : a[0];
    const top  = py < a[1] ? a[1] - h : a[1];
    cropRect = [Math.max(0, Math.min(W - w, left)), Math.max(0, Math.min(H - h, top)), w, h];
  } else if (cropMode === 'draw' && dragStart) {
    const x0 = Math.max(0, Math.min(W, dragStart[0]));
    const y0 = Math.max(0, Math.min(H, dragStart[1]));
    const x1 = Math.max(0, Math.min(W, p[0]));
    const y1 = Math.max(0, Math.min(H, p[1]));
    cropRect = [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
  }
  drawCrop();
});
crop.addEventListener('pointerup', () => { cropMode = null; dragStart = null; });
crop.addEventListener('pointercancel', () => { cropMode = null; dragStart = null; });

document.getElementById('cropCancel').onclick = () => modal.classList.remove('on');
document.getElementById('cropReset').onclick = () => { cropRect = null; drawCrop(); };
document.getElementById('cropOk').onclick = () => {
  if (!cropImg) return;
  const minSide = Math.max(16, Math.round(cropImg.width * 0.03));
  const r = (cropRect && cropRect[2] >= minSide && cropRect[3] >= minSide)
          ? cropRect : [0, 0, cropImg.width, cropImg.height];
  const c = document.createElement('canvas');
  // 布样统一压到 480px 以内：一张布的无缝图是原图 2×2 倍面积，8 张不能吃爆手机内存
  const shrink = Math.min(1, 480 / Math.max(8, r[2]), 480 / Math.max(8, r[3]));
  c.width = Math.max(8, Math.round(r[2] * shrink));
  c.height = Math.max(8, Math.round(r[3] * shrink));
  c.getContext('2d').drawImage(cropImg, r[0], r[1], r[2], r[3],
      0, 0, c.width, c.height);

  const im = new Image();
  im.onload = () => { addFabric(im); modal.classList.remove('on'); };
  im.src = c.toDataURL('image/png');
};

/* ---------- 标签页 ---------- */
/* 导出页：编号强制隐藏、选中也暂时清掉（导出图不带编号、也不带流动虚线），
   离开导出页时按"进来之前"的状态原样恢复（原来开着就开、原来关着就关，不强制打开） */
let curPane = 'paneRegion';
let numBeforeExport = null;      // null = 当前不在导出页
let selBeforeExport = null;      // 进导出页前的选中分区 id（含 null）
let inExport = false;
document.querySelectorAll('.tabs button').forEach(b => {
  b.onclick = () => {
    const next = b.dataset.pane;
    if (next === curPane) return;
    if (next === 'paneOut' && !inExport) {
      inExport = true;
      numBeforeExport = state.showNum;                 // 记住进入前的值
      state.showNum = false;
      document.getElementById('showNum').checked = false;
      selBeforeExport = state.selected;                // 高亮也先摘掉
      state.selected = null;
      buildRegionList(); buildSwatches(); syncTexUI(); render();
    } else if (curPane === 'paneOut' && next !== 'paneOut') {
      if (inExport) {
        inExport = false;
        state.showNum = numBeforeExport;               // 原样恢复，不强制打开
        document.getElementById('showNum').checked = numBeforeExport;
        numBeforeExport = null;
        state.selected = selBeforeExport;              // 选中也还原
        selBeforeExport = null;
        buildRegionList(); buildSwatches(); syncTexUI(); render();
      }
    }
    curPane = next;
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
    document.querySelectorAll('.pane').forEach(x => x.classList.toggle('on', x.id === next));
  };
});

/* ---------- 画布自适应（竖屏/横屏都不出滚动条） ---------- */
/* 底部抽屉：高度锁定成"页面打开时（停在分区页）的高度"，不随 tab 内容多少、也不随 dvh 动态变化 */
let sheetH = 0;
function lockSheetHeight(force){
  if (!sheetH || force) sheetH = Math.round(Math.min(window.innerHeight * 0.44, 340));
  document.documentElement.style.setProperty('--sheet-h', sheetH + 'px');
}

function fitCanvas(){
  const st = view.parentElement;
  const cs = getComputedStyle(st);
  const padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
  const padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
  const aw = Math.max(120, st.clientWidth - padL - padR);
  const ah = Math.max(120, st.clientHeight - padT - padB);
  const s = Math.min(aw / W, ah / H);
  view.style.width = Math.round(W * s) + 'px';
  view.style.height = Math.round(H * s) + 'px';
  if (ovC.width !== W || ovC.height !== H) { ovC.width = W; ovC.height = H; }
  ovC.style.width = view.style.width;
  ovC.style.height = view.style.height;
  const vr = view.getBoundingClientRect(), sr = st.getBoundingClientRect();
  ovC.style.left = Math.round(vr.left - sr.left) + 'px';
  ovC.style.top = Math.round(vr.top - sr.top) + 'px';
  drawSel();
  placeUndo();
  placeReset();
}

/* 重置按钮：钉在白底图左上角内侧，永远显示 */
function placeReset(){
  const rb = document.getElementById('resetBtn');
  if (!rb) return;
  rb.style.display = 'flex';
  const st = view.parentElement;
  const sr = st.getBoundingClientRect(), cr = view.getBoundingClientRect();
  rb.style.left = Math.round(cr.left - sr.left + 8) + 'px';
  rb.style.top  = Math.round(cr.top - sr.top + 8) + 'px';
}

/* 撤销按钮：钉在白底图右上角内侧，固定不挪（包体压到也不管，被挡就去「分区」列表里点） */
function placeUndo(){
  const ub = document.getElementById('undoBtn');
  if (!ub) return;
  const st = view.parentElement;
  const sr = st.getBoundingClientRect(), cr = view.getBoundingClientRect();
  ub.style.right = 'auto';
  ub.style.left = Math.round(cr.right - sr.left - (ub.offsetWidth || 46) - 8) + 'px';
  ub.style.top  = Math.round(cr.top - sr.top + 8) + 'px';
}

window.addEventListener('resize', () => { lockSheetHeight(true); fitCanvas(); });
window.addEventListener('orientationchange', () => setTimeout(() => { lockSheetHeight(true); fitCanvas(); }, 120));
if (window.matchMedia) {
  const mq = window.matchMedia('(orientation: portrait)');
  if (mq.addEventListener) mq.addEventListener('change', fitCanvas);
}

/* ---------- 颜色 ---------- */
const history = [];

function refreshUndo(){
  const b = document.getElementById('undoBtn');
  if (b) b.disabled = history.length === 0;
}

const HISTORY_MAX = 50;

// 快照只存"引用 + 参数"，绝不深拷 canvas：50 步 × 每步几 KB，图片数据不进栈
function snapshot(){
  const rf = {};
  META.forEach(r => {
    const e = state.regionFabric[r.id];
    if (e) rf[r.id] = {
      fabricId: e.fabricId,
      solid: e.solid,
      tex: e.tex ? Object.assign({}, e.tex) : null
    };
  });
  const hd = {};
  META.forEach(r => { if (state.hidden[r.id]) hd[r.id] = true; });
  history.push({ regionFabric: rf, activeFabricId: state.activeFabricId, hidden: hd });
  while (history.length > HISTORY_MAX) history.shift();
  refreshUndo();
}

function findSysFab(){
  for (let i = 0; i < state.fabrics.length; i++) if (state.fabrics[i].sys) return state.fabrics[i];
  return null;
}

// 临时提示：在顶部提示位淡入淡出一句话，不打断操作
let flashTimer = null;
function flashHint(msg){
  const el = document.getElementById('assignHint');
  if (!el) return;
  const old = el.textContent;
  el.textContent = msg;
  el.style.color = '#2563eb';
  if (flashTimer) clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.textContent = old; el.style.color = ''; }, 2200);
}

// 一键复原：所有分区刷回白底（纯白），清空面料；算 1 步，可撤销
function resetAll(){
  snapshot();
  META.forEach(r => {
    state.regionFabric[r.id] = { solid: '#ffffff' };
  });
  // 重置只刷颜色/面料，不恢复用户已删除的分区（hidden 状态保留）
  // 重置同时退出选中：画布上不留高亮虚线，也不再有分区被"选中"的上下文
  state.selected = null;
  buildRegionList(); buildFabricRow(); buildSwatches(); syncTexUI(); render();
}

function undo(){
  const h = history.pop();
  if (!h) return;
  state.regionFabric = h.regionFabric;
  state.activeFabricId = h.activeFabricId;
  state.hidden = h.hidden || {};
  buildRegionList(); buildFabricRow(); buildSwatches(); syncTexUI(); render(); refreshUndo();
}

// 只有"选中分区 + 点颜色"才铺色；没选分区点颜色不生效（说明文字里已写明）
function paintSolid(color){
  if (!state.selected) return;
  snapshot();
  const prev = state.regionFabric[state.selected];
  // 之前贴过的布保留记录，切回面料时能还原原来那套参数
  const keep = (prev && prev.fabricId) ? { fabricId: prev.fabricId, tex: prev.tex } : {};
  state.regionFabric[state.selected] = Object.assign({ solid: color }, keep);
  buildRegionList(); buildSwatches(); syncTexUI(); render();
}

function allSwatches(){
  const list = state.palette.slice();
  state.custom.forEach(c => { if (list.indexOf(c) < 0) list.push(c); });
  return list;
}

function addCustom(color){
  color = String(color || '').trim().toLowerCase();
  if (!/^#?[0-9a-f]{6}$/.test(color)) return null;
  if (color[0] !== '#') color = '#' + color;
  if (state.custom.indexOf(color) >= 0) return color;
  state.custom.push(color);
  while (state.custom.length > 10) state.custom.shift();
  return color;
}

// 删自定义色：只从色板拿掉入口，已经用它铺过的分区保持原样（颜色是数据，不是全局开关）
function removeCustom(color){
  const i = state.custom.indexOf(color);
  if (i < 0) return;
  state.custom.splice(i, 1);
  buildSwatches();
}

function buildSwatches(){
  const box = document.getElementById('swatches');
  if (!box) return;
  box.innerHTML = '';
  allSwatches().forEach(c => {
    const d = document.createElement('div');
    const rf = state.selected ? state.regionFabric[state.selected] : null;
    // 只有"当前选中分区就是这个颜色"才描环；不在别的分区上打点，免得像被占用
    const isReg = !!(rf && rf.solid === c);
    d.className = 'sw' + (isReg ? ' on' : '');
    d.style.background = c;
    d.title = c;
    d.onclick = () => paintSolid(c);
    // 只有用户自己加的颜色才给「×」；预置 10 色不删（删了色板就残了）
    if (state.custom.indexOf(c) >= 0) {
      const x = document.createElement('span');
      x.className = 'del'; x.textContent = '×'; x.title = '删掉这个颜色';
      x.onpointerdown = ev => ev.stopPropagation();
      x.onclick = ev => { ev.stopPropagation(); removeCustom(c); };
      d.appendChild(x);
    }
    box.appendChild(d);
  });
  // 更多颜色
  const more = document.createElement('div');
  more.className = 'sw more';
  more.id = 'swMore';
  more.textContent = '＋';
  more.title = '更多颜色';
  more.onclick = pickMore;
  box.appendChild(more);
}

function pickMore(){
  const cp = document.getElementById('colorPicker');
  if (!cp) return;
  cp.classList.add('on');
  cp.scrollIntoView({block: 'nearest'});
  updateColorPreview();
}

// 颜色选择器：色相 + 亮度
function updateColorPreview(){
  const h = document.getElementById('cpHue').value;
  const l = document.getElementById('cpLight').value;
  const s = 0.6; // 固定饱和度
  const color = hslToHex(h, s, l);
  document.getElementById('cpPreview').style.background = color;
  return color;
}

function hslToHex(h, s, l){
  l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = n => {
    const k = (n + h / 30) % 12;
    const color = l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    return Math.round(255 * color).toString(16).padStart(2, '0');
  };
  return '#' + f(0) + f(8) + f(4);
}

document.getElementById('cpHue').addEventListener('input', updateColorPreview);
document.getElementById('cpLight').addEventListener('input', updateColorPreview);

document.getElementById('cpCancel').onclick = () => {
  document.getElementById('colorPicker').classList.remove('on');
};

document.getElementById('cpOk').onclick = () => {
  const color = updateColorPreview();
  const c = addCustom(color);
  if (c) { buildSwatches(); flashHint('已加入色板，选个分区再点它上色'); }
  document.getElementById('colorPicker').classList.remove('on');
};

/* ---------- 取色器 / 色值：只加进色板，不直接刷到分区 ---------- */
document.getElementById('hexApply').onclick = () => {
  const v = document.getElementById('hexIn').value;
  const c = addCustom(v);
  if (!c) { document.getElementById('hexIn').value = ''; document.getElementById('hexIn').placeholder = '色值不对，试 #cfe9dd'; return; }
  document.getElementById('hexIn').value = '';
  document.getElementById('hexIn').placeholder = '已加入色板 ✓ 再输一个';
  buildSwatches();
  flashHint('已加入色板，选个分区再点它上色');
};
document.getElementById('hexIn').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('hexApply').click();
});

/* ---------- 撤销 ---------- */
document.getElementById('undoBtn').onclick = undo;
document.getElementById('resetBtn').onclick = resetAll;

/* ---------- 控件 ---------- */
document.getElementById('showNum').onchange = e => { state.showNum = e.target.checked; render(); };
// 滑杆：拖一次算一步（pointerdown 先快照），input 里连续改参数不重复入栈
function bindTexSlider(id, apply){
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('pointerdown', snapshot);
  el.addEventListener('keydown', snapshot);
  el.addEventListener('input', e => {
    const t = texTarget();
    if (!t) return;
    apply(t.tex, +e.target.value);
    render();
  });
}
bindTexSlider('pScale', (tex, v) => { tex.scale = v / 100; });
bindTexSlider('pRot', (tex, v) => { tex.rotate = v; });
bindTexSlider('pOff', (tex, v) => { tex.offset = v; });
document.getElementById('printOn').onchange = e => {
  state.print.on = e.target.checked;
  syncLineArtControls();
  render();
};
function syncLineArtControls(){
  const on = document.getElementById('printOn').checked;
  ['printColor','printOp','outlineOn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.disabled = !on; el.style.opacity = on ? '1' : '0.4'; el.style.pointerEvents = on ? '' : 'none'; }
  });
}
syncLineArtControls();
document.getElementById('printColor').onchange = e => { state.print.color = e.target.value; render(); };
document.getElementById('printOp').oninput = e => { state.print.op = e.target.value / 100; render(); };
document.getElementById('outlineOn').onchange = e => { state.outline = e.target.checked; render(); };
document.getElementById('lightAng').oninput = e => { state.light.deg = +e.target.value; render(); };
document.getElementById('lightInt').oninput = e => { state.light.inten = e.target.value / 100; render(); };

/* ---------- 全屏预览（缩放） ---------- */
const prevEl = document.getElementById('preview');
const prevCanvas = document.getElementById('prevCanvas');
const pctx = prevCanvas.getContext('2d');
let prevScale = 1;
let prevMinScale = 0.05;
const prevPointers = {};
let prevMode = null, prevDistStart = 0, prevScaleStart = 1;

function applyPreviewTransform(){
  prevCanvas.style.transform = 'translate(-50%, -50%) scale(' + prevScale + ')';
}
function fitPreview(){
  var aw = window.innerWidth * 0.8, ah = window.innerHeight * 0.8;
  var fit = Math.min(aw / prevCanvas.width, ah / prevCanvas.height, 1);
  prevScale = fit;
  prevMinScale = Math.max(0.05, fit * 0.25);
  applyPreviewTransform();
}
function bboxOfPath(d){
  var nums = d.match(/-?\d+\.?\d*/g);
  if (!nums) return null;
  var xs = [], ys = [];
  for (var i = 0; i < nums.length; i++) {
    var v = parseFloat(nums[i]);
    if (i % 2 === 0) xs.push(v); else ys.push(v);
  }
  return [Math.min.apply(null, xs), Math.min.apply(null, ys),
          Math.max.apply(null, xs), Math.max.apply(null, ys)];
}
function openPreviewCanvas(srcCv){
  // 清空上次手势残留
  Object.keys(prevPointers).forEach(k => delete prevPointers[k]);
  prevMode = null; prevDistStart = 0;
  try {
    var tmp = document.createElement('canvas');
    tmp.width = srcCv.width; tmp.height = srcCv.height;
    var tctx = tmp.getContext('2d');
    tctx.drawImage(srcCv, 0, 0);
    // 已删除的分区也抠掉
    tctx.globalCompositeOperation = 'destination-out';
    META.forEach(function(r){
      if (state.hidden[r.id]) tctx.fill(paths[r.id]);
    });
    // 按包体轮廓抠白底
    tctx.globalCompositeOperation = 'destination-in';
    tctx.fillStyle = '#000';
    tctx.fill(silPath);
    // 紧裁切
    var b = bboxOfPath(SIL);
    if (b) {
      var bx0 = Math.floor(b[0]), by0 = Math.floor(b[1]);
      var bx1 = Math.ceil(b[2]), by1 = Math.ceil(b[3]);
      var cw = Math.max(8, bx1 - bx0), ch = Math.max(8, by1 - by0);
      prevCanvas.width = cw; prevCanvas.height = ch;
      pctx.clearRect(0, 0, cw, ch);
      pctx.drawImage(tmp, bx0, by0, cw, ch, 0, 0, cw, ch);
    } else {
      prevCanvas.width = srcCv.width; prevCanvas.height = srcCv.height;
      pctx.drawImage(tmp, 0, 0);
    }
  } catch(e) {
    // 兜底：直接画原画布
    prevCanvas.width = srcCv.width; prevCanvas.height = srcCv.height;
    pctx.drawImage(srcCv, 0, 0);
  }
  prevEl.classList.add('on');
  fitPreview();
}
function openPreviewImage(url){
  var im = new Image();
  im.onload = function(){
    prevCanvas.width = im.naturalWidth || im.width;
    prevCanvas.height = im.naturalHeight || im.height;
    pctx.drawImage(im, 0, 0);
    prevEl.classList.add('on');
    fitPreview();
  };
  im.src = url;
}
document.getElementById('prevClose').onclick = function(){
  Object.keys(prevPointers).forEach(k => delete prevPointers[k]);
  prevMode = null; prevDistStart = 0;
  prevEl.classList.remove('on');
};
prevEl.addEventListener('click', function(e){
  if (e.target === prevEl) {
    Object.keys(prevPointers).forEach(k => delete prevPointers[k]);
    prevMode = null; prevDistStart = 0;
    prevEl.classList.remove('on');
  }
});
prevCanvas.addEventListener('dblclick', fitPreview);

// 桌面端滚轮缩放
prevEl.addEventListener('wheel', function(e){
  e.preventDefault();
  var factor = e.deltaY < 0 ? 1.1 : 0.9;
  prevScale = Math.max(prevMinScale, Math.min(6, prevScale * factor));
  applyPreviewTransform();
}, {passive: false});

// 移动端双指捏合
prevCanvas.addEventListener('pointerdown', function(e){
  prevPointers[e.pointerId] = {x: e.clientX, y: e.clientY};
  try { prevCanvas.setPointerCapture(e.pointerId); } catch(err){}
  var n = Object.keys(prevPointers).length;
  if (n === 2) {
    prevMode = 'pinch';
    prevDistStart = pointerDist();
    prevScaleStart = prevScale;
  }
});
prevCanvas.addEventListener('pointermove', function(e){
  if (!(e.pointerId in prevPointers)) return;
  prevPointers[e.pointerId] = {x: e.clientX, y: e.clientY};
  if (prevMode === 'pinch' && Object.keys(prevPointers).length === 2) {
    var d = pointerDist();
    if (prevDistStart > 0) prevScale = Math.max(prevMinScale, Math.min(6, prevScaleStart * d / prevDistStart));
    applyPreviewTransform();
  }
});
function pointerDist(){
  var pts = Object.keys(prevPointers).map(function(k){ return prevPointers[k]; });
  if (pts.length < 2) return 0;
  return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
}
function endPrevPointer(e){
  delete prevPointers[e.pointerId];
  if (Object.keys(prevPointers).length < 2) prevMode = null;
}
prevCanvas.addEventListener('pointerup', endPrevPointer);
prevCanvas.addEventListener('pointercancel', endPrevPointer);

/* ---------- 暂存配色（缩略图格子 + 一键还原） ---------- */
const SNAP_MAX = 10;
function snapshotState(){
  const rf = {};
  META.forEach(r => {
    const e = state.regionFabric[r.id];
    if (e) rf[r.id] = JSON.parse(JSON.stringify(e));
  });
  const hd = {};
  META.forEach(r => { if (state.hidden[r.id]) hd[r.id] = true; });
  return { regionFabric: rf, hidden: hd, activeFabricId: state.activeFabricId };
}
function restoreState(snap){
  snapshot();
  state.regionFabric = JSON.parse(JSON.stringify(snap.state.regionFabric));
  state.hidden = Object.assign({}, snap.state.hidden);
  state.activeFabricId = snap.state.activeFabricId;
  state.selected = null;
  buildRegionList(); buildFabricRow(); buildSwatches(); syncTexUI(); render();
  flashHint('已应用暂存方案');
}
function buildSnapRow(){
  const row = document.getElementById('snapRow');
  const head = document.getElementById('snapHead');
  row.innerHTML = '';
  head.style.display = state.snapshots.length ? 'flex' : 'none';
  state.snapshots.forEach((snap, i) => {
    const d = document.createElement('div');
    d.className = 'snapcell';
    const im = new Image(); im.src = snap.thumb;
    d.appendChild(im);
    const del = document.createElement('span');
    del.className = 'del'; del.textContent = '×';
    del.onclick = ev => { ev.stopPropagation(); state.snapshots.splice(i, 1); buildSnapRow(); };
    d.appendChild(del);
    const ap = document.createElement('span');
    ap.className = 'apply'; ap.textContent = '应用';
    ap.onclick = ev => { ev.stopPropagation(); restoreState(snap); };
    d.appendChild(ap);
    row.appendChild(d);
  });
}
document.getElementById('btnSnapshot').onclick = () => {
  const tw = 200, th = Math.round(200 * H / W);
  const c = document.createElement('canvas');
  c.width = tw; c.height = th;
  c.getContext('2d').drawImage(view, 0, 0, tw, th);
  const thumb = c.toDataURL('image/jpeg', 0.72);
  state.snapshots.push({ thumb: thumb, state: snapshotState() });
  while (state.snapshots.length > SNAP_MAX) state.snapshots.shift();
  buildSnapRow();
  flashHint('已暂存当前配色');
};
document.getElementById('btnPreview').onclick = () => openPreviewCanvas(view);

/* ---------- 导出（只给成品图：编号图/分区 SVG/面料清单由 Step 3 的 regions_extract 产出，页面不再重复实现） ---------- */
function download(url, name){
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
}
/* 成品图导出
   桌面：直接 a[download]（blob）
   手机/内嵌 WebView：主路径 = 系统分享面板（navigator.share 带文件 → 面板里「存储图像」），
     不可用时退回出图层；出图层里的图用 data: URL —— blob: 图片系统取不到，长按「存储图像」必失败 */
const EXP_NAME = '袋型敷色成品.png';
function isMobileUA(){
  return /iPhone|iPad|iPod|Android|Mobile|MicroMessenger|Doubao|QQ/i.test(navigator.userAgent)
      || Math.min(window.innerWidth, window.innerHeight) < 620;
}
let dlFile = null, dlDataURL = '';      // 导出时缓存一次：点「保存图片」时不再等 toBlob，保住 iOS 的用户手势
function canvasBlob(){
  return new Promise(res => view.toBlob ? view.toBlob(res, 'image/png') : res(null));
}
async function prepareExport(){
  const blob = await canvasBlob();
  dlFile = blob ? new File([blob], EXP_NAME, {type: 'image/png'}) : null;
  dlDataURL = view.toDataURL('image/png');
}
/* 同步发起分享：必须在点击手势内调用，前头不要有 await */
function shareNow(){
  if (!dlFile || typeof navigator.share !== 'function') return Promise.resolve(false);
  try {
    if (navigator.canShare && !navigator.canShare({files: [dlFile]})) return Promise.resolve(false);
    return navigator.share({files: [dlFile]}).then(() => true).catch(e => !!(e && e.name === 'AbortError'));
  } catch (e) { return Promise.resolve(false); }
}
function showDl(){
  const img = document.getElementById('dlImg');
  img.src = dlDataURL;                    // data: 而不是 blob:，长按「存储图像」才存得进去
  document.getElementById('dlTip').textContent = '优先点「系统分享」；也可以长按图片，选「存储图像」或「保存图片」';
  document.getElementById('dl').classList.add('on');
}
document.getElementById('expPng').onclick = async () => {
  const btn = document.getElementById('expPng');
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '生成中…';
  try {
    await prepareExport();
    // 桌面端走 a[download] 直下
    if (!isMobileUA() && dlFile) {
      const a = document.createElement('a');
      a.href = dlDataURL;
      a.download = EXP_NAME;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }
    // 手机端：先弹出保存层，由层内按钮同步调用分享，保住用户手势
    showDl();
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
};
document.getElementById('dlClose').onclick = () => {
  const img = document.getElementById('dlImg');
  document.getElementById('dl').classList.remove('on');
  img.removeAttribute('src');
};
document.getElementById('dlSave').onclick = async () => {
  const ok = await shareNow();            // 缓存已就绪，不需要再等画布编码，手势不丢
  document.getElementById('dlTip').textContent = ok
    ? '已打开系统分享面板，选「存储图像」即可'
    : '当前浏览器不支持网页内直接存图。可以：① 长按图片选「保存图片」；② 点「复制链接」，粘到手机自带浏览器（Safari / Chrome）打开后再导出；③ 系统截屏';
};
document.getElementById('dlCopy').onclick = async () => {
  const tip = document.getElementById('dlTip');
  const link = location.href;
  // 本地文件打开时，复制 file:// 链接没用，直接提示
  if (link.startsWith('file://')) {
    tip.textContent = '请用手机自带浏览器（Safari / Chrome）打开本页面后，再点导出就能存图';
    return;
  }
  try {
    await navigator.clipboard.writeText(link);
    tip.textContent = '链接已复制。粘到手机自带浏览器（Safari / Chrome）打开，再点导出就能存图';
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = link; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); tip.textContent = '链接已复制，粘到手机自带浏览器打开后再导出'; }
    catch (err) { tip.textContent = '复制失败，请手动复制地址栏里的链接，用手机自带浏览器打开'; }
    document.body.removeChild(ta);
  }
};

/* ---------- 初始化 ---------- */
// 构建时传入的初始布样（--fabric）：进面料架、不占用户 8 格、不抢 activeFabricId。
// 之前只把 dataURL 塞进 PRESET_FABRIC 却没人实例化，结果“传了 --fabric 页面里也是空的”。
if (PRESET_FABRIC && PRESET_FABRIC.indexOf('data:image') === 0) {
  loadFabric(PRESET_FABRIC, '初始布样', true);
}
lockSheetHeight(true);
buildRegionList();
buildFabricRow();
buildSwatches();
syncTexUI();
render();
fitCanvas();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()

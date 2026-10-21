# -*- coding: utf-8 -*-
"""
POD 批量出图(ERP 版): 从 ERP 图案库拉待生成的提示词 -> ComfyUI 出图 -> 套图 -> 回写 ERP。

取代 feishu_batch.py。提示词、状态、评分现在全在 ERP 里, 飞书不再参与。

用法(先确保 ComfyUI 开着):
    python erp_batch.py --dry-run     # 只看要跑什么
    python erp_batch.py               # 跑
    python erp_batch.py --limit 3     # 先跑几条试试
    python erp_batch.py --reset-stuck # 把卡在"生成中"的放回"待生成"

ERP 地址和账号在上一级目录的 .env 里(ERP_URL / ERP_USER / ERP_PASS)。
"""

import argparse
import base64
import io
import os
import random
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_batch          # noqa: E402
import compose_mockups      # noqa: E402
import paths                # noqa: E402
import push_to_erp          # noqa: E402

# 用哪张套图当 ERP 里的预览(底图_印刷位)
PREVIEW = "white_front"
JPEG_WIDTH = 760
JPEG_QUALITY = 85

# 上架用的各颜色主图: 尺寸要够大(商品主图), 质量高一些
MAIN_WIDTH = 1024
MAIN_QUALITY = 90
MAIN_PLACEMENT = "front"   # 上架主图用正面大图那版


def log(msg):
    print(msg, flush=True)


def to_jpeg_b64(path, width=JPEG_WIDTH, quality=JPEG_QUALITY):
    image = Image.open(path).convert("RGB")
    if image.width > width:
        h = round(image.height * width / image.width)
        image = image.resize((width, h), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def has_chinese(text):
    return any("一" <= c <= "鿿" for c in text)


def main():
    parser = argparse.ArgumentParser(description="从 ERP 拉提示词批量出图")
    parser.add_argument("--dry-run", action="store_true", help="只看要跑什么")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几条")
    parser.add_argument("--workflow", default=comfy_batch.WORKFLOW_FILE)
    parser.add_argument("--reset-stuck", action="store_true", help="卡在生成中的放回待生成")
    parser.add_argument("--no-writeback", action="store_true", help="不回写 ERP")
    args = parser.parse_args()

    if args.reset_stuck:
        r = push_to_erp.call("/api/designs/reset-stuck", {})
        log("已把 %s 条放回「待生成」" % r.get("n", "?"))
        return 0

    graph = comfy_batch.load_workflow(args.workflow)
    if not args.dry_run and not comfy_batch.is_alive():
        log("连不上 ComfyUI (%s)。先把 ComfyUI 打开。ERP 一个字都没动。" % comfy_batch.COMFY_HOST)
        return 1

    log("从 ERP 拉待生成的提示词 ...")
    data = push_to_erp.call("/api/designs/pending?limit=500", method="GET")
    rows = data.get("rows") or []
    if args.limit:
        rows = rows[:args.limit]
    log("待生成 %d 条" % len(rows))

    plan = []
    for r in rows:
        prompt = (r.get("prompt") or "").strip()
        if not prompt:
            continue
        if has_chinese(prompt):
            log("  [%s] 跳过: 提示词里有中文, FLUX 只认英文" % r.get("code"))
            continue
        seed_raw = str(r.get("seed") or "").strip()
        seed = int(seed_raw) if seed_raw.isdigit() else random.randint(0, 2 ** 32 - 1)
        slug = comfy_batch.make_slug({"主体": r.get("subject") or prompt.split(",")[0]}, 0)
        plan.append({"id": r["id"], "code": str(r["code"]), "prompt": prompt,
                     "seed": seed, "slug": slug, "batch": r.get("batch") or ""})

    if args.dry_run:
        for p in plan:
            log("  [%s] %s  seed=%d" % (p["code"], p["slug"], p["seed"]))
            log("        %s..." % p["prompt"][:100])
        log("")
        log("演练结束: 会跑 %d 条。去掉 --dry-run 就真跑。" % len(plan))
        return 0
    if not plan:
        log("没有要跑的。去 ERP 图案库新增提示词。")
        return 0

    # 先认领, 免得另一台机器抢同一批
    if not args.no_writeback:
        push_to_erp.call("/api/designs/claim", {"ids": [p["id"] for p in plan]})

    templates = compose_mockups.load_templates()
    done = failed = 0
    for index, p in enumerate(plan, start=1):
        log("[%s] %s  (%d/%d)  seed=%d" % (p["code"], p["slug"], index, len(plan), p["seed"]))
        job = comfy_batch.build_job(graph, p["prompt"], p["seed"], p["code"], p["slug"])
        try:
            prompt_id = comfy_batch.submit(job)
        except comfy_batch.ComfyDown as e:
            log("!! %s" % e)
            log("   已中断。没跑到的还在「生成中」, 用 --reset-stuck 放回待生成。")
            return 1
        except RuntimeError as e:
            log("        x 提交失败: %s" % e)
            failed += 1
            continue

        try:
            comfy_batch.wait_until_idle()
        except (KeyboardInterrupt, RuntimeError) as e:
            log("   中断: %s" % e)
            return 1

        state, detail = comfy_batch.fetch_result(prompt_id)
        log("        %s  %s" % (state, detail))
        if state != "成功":
            failed += 1
            if not args.no_writeback:
                push_to_erp.call("/api/designs/edit", {"id": p["id"], "status": "生成失败"})
            continue

        # 出图 -> 套图 -> 回写
        outputs = comfy_batch.fetch_outputs(prompt_id)
        png = (outputs.get(comfy_batch_png_node()) or [])
        body = {"code": p["code"], "prompt": p["prompt"], "seed": str(p["seed"]),
                "batch": p["batch"], "status": "已生成"}
        if png:
            subfolder, filename = png[0]
            body["png_name"] = filename
            png_path = os.path.join(paths.COMFY_ROOT, "output", subfolder, filename)
            try:
                mockups = compose_mockups.compose_one(png_path, templates)
                preview = mockups.get(PREVIEW)
                if preview:
                    body["img_b64"] = to_jpeg_b64(preview)
                # 各颜色主图, 上架时每个 SKC 要用自己的那张
                mains = {}
                for color_key in templates:
                    m = mockups.get("%s_%s" % (color_key, MAIN_PLACEMENT))
                    if m:
                        mains[color_key] = to_jpeg_b64(m, MAIN_WIDTH, MAIN_QUALITY)
                if mains:
                    body["imgs_b64"] = mains
                    log("        %d 个颜色主图" % len(mains))
            except Exception as e:
                log("        ! 套图失败(透明图已出): %s" % e)
        if not args.no_writeback:
            try:
                push_to_erp.call("/api/designs", body)
                log("        → 已回写 ERP")
            except Exception as e:
                log("        ! 回写失败(图在本地): %s" % e)
        done += 1

    log("")
    log("完成: 成功 %d 条, 失败 %d 条" % (done, failed))
    log("去 %s/designs 打分。" % push_to_erp.ERP_URL)
    return 1 if failed else 0


def comfy_batch_png_node():
    """出透明图的保存节点编号, 跟着 comfy_batch 的配置走, 不写死"""
    for nid, tpl in comfy_batch.SAVE_PREFIX.items():
        if tpl.startswith("png/"):
            return nid
    return "44"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

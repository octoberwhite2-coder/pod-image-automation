# -*- coding: utf-8 -*-
"""
把飞书提示词库的全部记录迁进 ERP 图案库。

迁什么: 编号/批次/提示词/seed/系列/主体/配色/灵感来源/备注/出图文件名,
        以及最重要的 评分 和 淘汰原因 —— 那是下一轮优化的全部燃料。
图片:   用本地 output/mockup 里的精准套图, 压成 JPEG 再传(比 PNG 省 4 倍)。

飞书原表不动。迁完自己核对无误, 再决定停用飞书。

用法:
    python migrate_feishu_to_erp.py --dry-run   # 只看要迁什么
    python migrate_feishu_to_erp.py             # 真迁
    python migrate_feishu_to_erp.py --verify    # 迁完逐条比对
"""

import argparse
import base64
import io
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feishu_schema as fs   # noqa: E402
import paths                 # noqa: E402
import push_to_erp           # noqa: E402  复用带认证的请求

TABLE_ID = "EXAMPLE_FEISHU_ID"
MOCKUP_SUFFIX = "_white_front.png"   # 用白T正面大图当预览

# 预览图压缩参数。套图是照片底图, JPEG 足够, 体积能从 600KB 降到 ~150KB
JPEG_WIDTH = 760
JPEG_QUALITY = 85


def log(msg):
    print(msg, flush=True)


def mockup_path(png_name):
    """出图文件名 21_xxx_00001_.png -> 套图 21_xxx_white_front.png"""
    if not png_name:
        return None
    stem = png_name.rsplit("_0", 1)[0]
    p = os.path.join(paths.COMFY_ROOT, "output", "mockup", stem + MOCKUP_SUFFIX)
    return p if os.path.exists(p) else None


def to_jpeg_b64(path):
    """套图转成 JPEG 的 base64, 顺便缩到合适宽度"""
    image = Image.open(path).convert("RGB")
    if image.width > JPEG_WIDTH:
        h = round(image.height * JPEG_WIDTH / image.width)
        image = image.resize((JPEG_WIDTH, h), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), buf.tell()


def collect():
    """从飞书读出所有要迁的记录"""
    token = fs.get_token()
    records = fs.list_all_records(token, TABLE_ID)
    rows = []
    for record in records:
        f = record.get("fields") or {}
        code = fs.field_text(f.get("编号"))
        prompt = fs.field_text(f.get("完整提示词"))
        if not code or not prompt:
            continue          # 空行不迁
        reasons = [x.get("name") if isinstance(x, dict) else str(x)
                   for x in (f.get("淘汰原因") or [])]
        png_name = fs.field_text(f.get("出图文件名"))
        rows.append({
            "code": code,
            "batch": fs.field_text(f.get("批次")),
            "prompt": prompt,
            "seed": fs.field_text(f.get("seed")),
            "series": fs.field_text(f.get("系列")),
            "subject": fs.field_text(f.get("主体")),
            "palette": fs.field_text(f.get("配色")),
            "source": fs.field_text(f.get("灵感来源")),
            "note": fs.field_text(f.get("备注")),
            "png_name": png_name,
            "score": fs.field_text(f.get("评分")),
            "reasons": ",".join(reasons),
            "_mockup": mockup_path(png_name),
        })
    rows.sort(key=lambda r: int(r["code"]) if r["code"].isdigit() else 0)
    return rows


def main():
    parser = argparse.ArgumentParser(description="飞书 -> ERP 图案库迁移")
    parser.add_argument("--dry-run", action="store_true", help="只看不迁")
    parser.add_argument("--verify", action="store_true", help="只做迁移后比对")
    args = parser.parse_args()

    if args.verify:
        return verify()

    rows = collect()
    scored = sum(1 for r in rows if r["score"])
    with_img = sum(1 for r in rows if r["_mockup"])
    log("飞书有效记录 %d 条 | 已评分 %d 条 | 能配上套图 %d 条" % (len(rows), scored, with_img))
    missing = [r["code"] for r in rows if not r["_mockup"]]
    if missing:
        log("  ! 这些找不到本地套图, 会只迁文字不带图: %s" % ",".join(missing))

    if args.dry_run:
        log("")
        for r in rows[:6]:
            log("  [%s] %s | 评分=%s %s | 图=%s"
                % (r["code"], r["batch"], r["score"] or "-",
                   ("(" + r["reasons"] + ")") if r["reasons"] else "",
                   "有" if r["_mockup"] else "无"))
        log("  ... 共 %d 条" % len(rows))
        log("")
        log("演练结束, ERP 没动。去掉 --dry-run 就真迁。")
        return 0

    ok = fail = 0
    total_kb = 0
    for r in rows:
        body = {k: v for k, v in r.items() if not k.startswith("_")}
        if r["_mockup"]:
            try:
                b64, size = to_jpeg_b64(r["_mockup"])
                body["img_b64"] = b64
                total_kb += size / 1024.0
            except Exception as e:
                log("  [%s] 图片处理失败(仍迁文字): %s" % (r["code"], e))
        try:
            result = push_to_erp.call("/api/designs", body)
            if result.get("ok"):
                ok += 1
                log("  [%s] %s %s" % (r["code"], "更新" if result.get("updated") else "新建",
                                      ("· " + r["score"]) if r["score"] else ""))
            else:
                fail += 1
                log("  [%s] × %s" % (r["code"], result.get("msg")))
        except Exception as e:
            fail += 1
            log("  [%s] × %s" % (r["code"], e))

    log("")
    log("迁移完成: 成功 %d 条, 失败 %d 条, 图片合计 %.1f MB" % (ok, fail, total_kb / 1024))
    log("跑 --verify 做逐条比对。")
    return 1 if fail else 0


def verify():
    """逐条比对飞书和 ERP, 重点是评分和淘汰原因不能丢"""
    rows = {r["code"]: r for r in collect()}
    result = push_to_erp.call("/api/designs?score=&batch=&q=", method="GET")
    erp = {str(x.get("code")): x for x in (result.get("rows") or [])}

    log("飞书 %d 条, ERP %d 条" % (len(rows), len(erp)))
    problems = []
    for code, src in rows.items():
        dst = erp.get(code)
        if not dst:
            problems.append("[%s] ERP 里没有" % code)
            continue
        if (src["score"] or "") != (dst.get("score") or ""):
            problems.append("[%s] 评分不一致: 飞书=%s ERP=%s" % (code, src["score"], dst.get("score")))
        if (src["reasons"] or "") != (dst.get("reasons") or ""):
            problems.append("[%s] 淘汰原因不一致: 飞书=%s ERP=%s"
                            % (code, src["reasons"], dst.get("reasons")))
        if (src["prompt"] or "").strip() != (dst.get("prompt") or "").strip():
            problems.append("[%s] 提示词不一致" % code)
        if src["_mockup"] and not dst.get("img"):
            problems.append("[%s] 本地有套图但 ERP 没图" % code)

    if problems:
        log("")
        log("发现 %d 处不一致:" % len(problems))
        for p in problems:
            log("  " + p)
        return 1
    log("")
    log("逐条比对通过: 评分、淘汰原因、提示词、图片全部一致。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

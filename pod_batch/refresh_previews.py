# -*- coding: utf-8 -*-
"""
把飞书表里的预览图换成 compose_mockups 生成的精准套图。

为什么要刷: 早期的预览图是 ComfyUI 工作流里套的 —— 整张画布缩到固定尺寸、
贴死坐标, 图案大小和位置都不准。现在套图改由 compose_mockups 按真实印刷区
裁边+等比缩放+居中定位, 表里应该换成这一版, 你打分看到的才是实际印刷效果。

用法:
    python refresh_previews.py --dry-run   # 只看会换哪些, 不动飞书
    python refresh_previews.py             # 真的换
    python refresh_previews.py --only 15 21

只处理"出图文件名"能对应上本地套图的记录; 对不上的会列出来, 不会乱改。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feishu_batch          # noqa: E402  复用上传附件的逻辑
import feishu_schema as fs   # noqa: E402
import paths                 # noqa: E402

TABLE_ID = os.environ.get("FEISHU_PROMPT_TABLE_ID", "")
TEMPLATE = "white_front"     # 用白T正面大图当预览(底图_印刷位)


def log(msg):
    print(msg, flush=True)


def main():
    parser = argparse.ArgumentParser(description="刷新飞书预览图为精准套图")
    parser.add_argument("--dry-run", action="store_true", help="只看不改")
    parser.add_argument("--only", nargs="*", help="只处理这些编号")
    args = parser.parse_args()

    mockup_dir = os.path.join(paths.COMFY_ROOT, "output", "mockup")
    if not os.path.isdir(mockup_dir):
        raise SystemExit("找不到套图目录: %s\n先跑 python compose_mockups.py" % mockup_dir)

    fs.require("FEISHU_PROMPT_TABLE_ID")
    token = fs.get_token()
    records = sorted(fs.list_all_records(token, TABLE_ID),
                     key=lambda r: int(fs.field_text(r["fields"].get("编号")) or 0))

    todo, missing = [], []
    for record in records:
        fields = record.get("fields") or {}
        item_id = fs.field_text(fields.get("编号"))
        filename = fs.field_text(fields.get("出图文件名"))
        if not filename:
            continue
        if args.only and item_id not in set(args.only):
            continue
        # 出图文件名形如 21_xxx_00001_.png, 套图是 21_xxx_white.png
        stem = filename.rsplit("_0", 1)[0]
        path = os.path.join(mockup_dir, "%s_%s.png" % (stem, TEMPLATE))
        if os.path.exists(path):
            todo.append((item_id, record["record_id"], path))
        else:
            missing.append((item_id, os.path.basename(path)))

    for item_id, want in missing:
        log("  ! 编号%s 找不到套图 %s, 跳过" % (item_id, want))

    log("待刷新 %d 条" % len(todo))
    if args.dry_run:
        for item_id, _, path in todo:
            log("  [%s] -> %s" % (item_id, os.path.basename(path)))
        log("")
        log("演练结束, 飞书没动。去掉 --dry-run 就真刷。")
        return 0
    if not todo:
        return 0

    done = failed = 0
    for item_id, record_id, path in todo:
        try:
            with open(path, "rb") as f:
                content = f.read()
            file_token = feishu_batch.upload_attachment(token, os.path.basename(path), content)
            # 直接覆盖整个附件列表, 旧的那张不精准的就没了
            fs.update_record(token, TABLE_ID, record_id,
                             {"预览图": [{"file_token": file_token}]})
            log("  [%s] √ %s" % (item_id, os.path.basename(path)))
            done += 1
        except Exception as e:
            log("  [%s] × 失败: %s" % (item_id, e))
            failed += 1

    log("")
    log("完成: 成功 %d 条, 失败 %d 条" % (done, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

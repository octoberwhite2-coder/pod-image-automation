# -*- coding: utf-8 -*-
"""
把一批新提示词写进飞书表(状态自动设为"待生成", 等着出图)。

输入是一个 JSON 文件, 形如:
[
  {"提示词": "....", "系列": "节日", "灵感来源": "halloween tee 爆款", "备注": "试试满版构图"},
  {"提示词": "...."}
]
只有"提示词"是必填的, 其余可省。批次统一由 --batch 指定。

用法:
    python add_prompts.py 新一批.json --batch R1-万圣节
    python add_prompts.py 新一批.json --batch R1-万圣节 --dry-run
"""

import argparse
import json
import os
import sys

import feishu_schema as fs

TABLE_ID = os.environ.get("FEISHU_PROMPT_TABLE_ID", "")
VALID_SERIES = {"动物", "节日", "复古", "物件", "卡通形象"}


def log(msg):
    print(msg, flush=True)


def has_chinese(text):
    return any("一" <= c <= "鿿" for c in text)


def main():
    parser = argparse.ArgumentParser(description="批量写入新提示词")
    parser.add_argument("json_file", help="提示词 JSON 文件")
    parser.add_argument("--batch", required=True, help="批次名, 例如 R1-万圣节")
    parser.add_argument("--dry-run", action="store_true", help="只检查不写入")
    args = parser.parse_args()

    with open(args.json_file, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        raise SystemExit("JSON 顶层要是一个数组")

    fs.require("FEISHU_PROMPT_TABLE_ID")
    token = fs.get_token()
    existing = {fs.field_text((r.get("fields") or {}).get("完整提示词"))
                for r in fs.list_all_records(token, TABLE_ID)}

    rows = []
    problems = []
    for index, item in enumerate(items, start=1):
        prompt = (item.get("提示词") or "").strip()
        if not prompt:
            problems.append("第%d条: 提示词是空的" % index)
            continue
        if has_chinese(prompt):
            problems.append("第%d条: 提示词里有中文, FLUX 只认英文" % index)
            continue
        if prompt in existing:
            problems.append("第%d条: 和表里已有的完全重复, 跳过" % index)
            continue

        row = {"完整提示词": prompt, "状态": "待生成", "批次": args.batch}
        series = (item.get("系列") or "").strip()
        if series:
            if series in VALID_SERIES:
                row["系列"] = series
            else:
                problems.append("第%d条: 系列「%s」不在选项里, 已忽略这一列" % (index, series))
        for key in ("主体", "配色", "灵感来源", "备注", "seed"):
            value = (item.get(key) or "").strip()
            if value:
                row[key] = value
        rows.append(row)
        existing.add(prompt)

    for p in problems:
        log("  ! " + p)

    log("")
    log("待写入 %d 条, 批次「%s」" % (len(rows), args.batch))
    for row in rows[:5]:
        log("  %s..." % row["完整提示词"][:100])
    if len(rows) > 5:
        log("  ... 另外 %d 条" % (len(rows) - 5))

    if args.dry_run:
        log("")
        log("演练结束, 没有写入。去掉 --dry-run 就真写。")
        return 0
    if not rows:
        log("没有可写入的内容。")
        return 1

    created = fs.batch_create(token, TABLE_ID, rows)
    log("")
    log("已写入 %d 条。现在可以双击「一键批量出图.bat」出图了。" % created)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

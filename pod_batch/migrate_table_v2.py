# -*- coding: utf-8 -*-
"""
一次性迁移: 把提示词库表从「三段拼接」改成「自由提示词 + 评分反馈」。

为什么要改: 原来"完整提示词"是公式, 把 主体/风格/配色 套进一个写死的模板。
实测 10 条提示词有 86% 的词组完全相同, 构图和画风被钉死, 所以出图必然雷同。

改成什么样:
  完整提示词  公式 -> 普通文本   每条可以完全不同, 由智能体写入
  主体/风格/配色  保留, 但降级成事后分析用的标签, 不再参与拼接
  新增 评分/淘汰原因/灵感来源/批次   这是反馈闭环的燃料

关键: 公式字段本身不存数据, 直接改类型会全部变空。
所以先把现有内容抓下来存到本地, 改完类型再写回去。
"""

import json
import os
import sys

import feishu_schema as fs

TABLE_ID = os.environ.get("FEISHU_PROMPT_TABLE_ID", "")
HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "_migrate_snapshot.json")

F_PROMPT = "完整提示词"

RATING_OPTIONS = ["优秀", "可用", "淘汰"]
REJECT_OPTIONS = ["抠图差", "风格雷同", "构图差", "配色不对", "主体畸形", "侵权风险", "不适合印花"]


def log(msg):
    print(msg, flush=True)


def main():
    fs.require("FEISHU_PROMPT_TABLE_ID")
    token = fs.get_token()

    # ---- 1. 先把公式算出来的提示词抓下来存盘 ----
    records = fs.list_all_records(token, TABLE_ID)
    snapshot = {}
    for record in records:
        text = fs.field_text((record.get("fields") or {}).get(F_PROMPT))
        if text:
            snapshot[record["record_id"]] = text
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    log("已备份 %d 条现有提示词 -> %s" % (len(snapshot), os.path.basename(SNAPSHOT)))

    # ---- 2. 公式字段改成普通文本 ----
    field = fs.list_fields(token, TABLE_ID)[F_PROMPT]
    if field["ui_type"] == "Formula":
        fs.call("%s/%s" % (fs.fields_path(TABLE_ID), field["field_id"]),
                token=token, method="PUT",
                body={"field_name": F_PROMPT, "type": fs.TYPE_TEXT})
        log("「%s」已从公式改成普通文本" % F_PROMPT)
    else:
        log("「%s」已经是 %s, 不用改" % (F_PROMPT, field["ui_type"]))

    # ---- 3. 把备份的内容写回去 ----
    restored = 0
    for record_id, text in snapshot.items():
        fs.update_record(token, TABLE_ID, record_id, {F_PROMPT: text})
        restored += 1
    log("已写回 %d 条提示词" % restored)

    # ---- 4. 新增反馈闭环需要的字段 ----
    new_fields = [
        ("评分", fs.TYPE_SINGLE_SELECT,
         {"options": [{"name": n} for n in RATING_OPTIONS]}, "你的判断, 优化的核心信号"),
        ("淘汰原因", fs.TYPE_MULTI_SELECT,
         {"options": [{"name": n} for n in REJECT_OPTIONS]}, "为什么不行, 可多选"),
        ("灵感来源", fs.TYPE_TEXT, None, "爆款关键词/热点/参考链接"),
        ("批次", fs.TYPE_TEXT, None, "第几轮生成的, 方便对比迭代效果"),
    ]
    for name, ftype, prop, note in new_fields:
        if fs.ensure_field(token, TABLE_ID, name, ftype, prop):
            log("新增字段「%s」 (%s)" % (name, note))
        else:
            log("字段「%s」已存在, 跳过" % name)

    # ---- 5. 已经出过图的老记录标成第 0 批 ----
    tagged = 0
    for record in fs.list_all_records(token, TABLE_ID):
        fields = record.get("fields") or {}
        if fs.field_text(fields.get("状态")) == "已生成" and not fs.field_text(fields.get("批次")):
            fs.update_record(token, TABLE_ID, record["record_id"], {"批次": "R0-固定模板"})
            tagged += 1
    if tagged:
        log("已把 %d 条老记录标为「R0-固定模板」, 方便日后对比" % tagged)

    log("")
    log("最终字段:")
    for name, field in fs.list_fields(token, TABLE_ID).items():
        prop = field.get("property") or {}
        extra = ""
        if prop.get("options"):
            extra = "  " + " / ".join(o["name"] for o in prop["options"])
        log("  %-8s %-14s%s" % (name, field["ui_type"], extra))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

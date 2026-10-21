# -*- coding: utf-8 -*-
"""
把飞书表里积累的判断整理成一份"当前学到了什么", 作为下一轮生成提示词的输入。

用法:
    python learnings.py           # 人看的摘要
    python learnings.py --raw     # 连完整提示词一起输出, 喂给智能体用

核心逻辑: 优秀样本告诉你什么该多做, 淘汰原因告诉你什么该避开,
用过的词频告诉你哪些套路已经用腻了(这是打破风格雷同的关键)。
"""

import argparse
import collections
import os
import sys

import feishu_schema as fs

TABLE_ID = os.environ.get("FEISHU_PROMPT_TABLE_ID", "")


def log(msg):
    print(msg, flush=True)


def phrases(prompt):
    return [p.strip().lower() for p in prompt.split(",") if p.strip()]


def main():
    parser = argparse.ArgumentParser(description="导出提示词库的经验积累")
    parser.add_argument("--raw", action="store_true", help="附上完整提示词原文")
    args = parser.parse_args()

    fs.require("FEISHU_PROMPT_TABLE_ID")
    token = fs.get_token()
    records = fs.list_all_records(token, TABLE_ID)

    rows = []
    for record in records:
        f = record.get("fields") or {}
        rows.append({
            "编号": fs.field_text(f.get("编号")),
            "提示词": fs.field_text(f.get("完整提示词")),
            "状态": fs.field_text(f.get("状态")),
            "评分": fs.field_text(f.get("评分")),
            "淘汰原因": [x.get("name") if isinstance(x, dict) else x
                     for x in (f.get("淘汰原因") or [])],
            "批次": fs.field_text(f.get("批次")),
            "灵感来源": fs.field_text(f.get("灵感来源")),
            "seed": fs.field_text(f.get("seed")),
        })

    rated = [r for r in rows if r["评分"]]
    good = [r for r in rows if r["评分"] == "优秀"]
    ok = [r for r in rows if r["评分"] == "可用"]
    bad = [r for r in rows if r["评分"] == "淘汰"]

    log("=" * 60)
    log("提示词库现状: 共 %d 条, 已出图 %d 条, 已打分 %d 条"
        % (len(rows), sum(1 for r in rows if r["状态"] == "已生成"), len(rated)))
    log("  优秀 %d | 可用 %d | 淘汰 %d" % (len(good), len(ok), len(bad)))
    log("=" * 60)

    # ---- 各批次的优秀率: 看迭代到底有没有进步 ----
    by_batch = collections.defaultdict(lambda: {"总": 0, "优秀": 0, "淘汰": 0})
    for r in rows:
        key = r["批次"] or "(未标批次)"
        by_batch[key]["总"] += 1
        if r["评分"] == "优秀":
            by_batch[key]["优秀"] += 1
        elif r["评分"] == "淘汰":
            by_batch[key]["淘汰"] += 1
    log("")
    log("--- 各批次表现 ---")
    for name in sorted(by_batch):
        s = by_batch[name]
        judged = s["优秀"] + s["淘汰"]
        rate = ("%.0f%%" % (s["优秀"] * 100.0 / judged)) if judged else "未打分"
        log("  %-16s 共%2d 条  优秀%2d 淘汰%2d  优秀率 %s"
            % (name, s["总"], s["优秀"], s["淘汰"], rate))

    # ---- 淘汰原因分布: 下一轮要专门避开的 ----
    reasons = collections.Counter()
    for r in bad:
        reasons.update(r["淘汰原因"] or ["(没填原因)"])
    if reasons:
        log("")
        log("--- 淘汰原因分布(下一轮重点规避) ---")
        for reason, count in reasons.most_common():
            log("  %-10s %d 次" % (reason, count))

    # ---- 词频: 哪些套路已经用滥了 ----
    used = collections.Counter()
    for r in rows:
        used.update(set(phrases(r["提示词"])))
    total = len([r for r in rows if r["提示词"]])
    overused = [(p, c) for p, c in used.most_common() if total and c >= total * 0.6]
    if overused:
        log("")
        log("--- 出现在 60% 以上提示词里的词组(已经用滥, 新一批要换掉) ---")
        for phrase, count in overused[:30]:
            log("  %-42s %d/%d" % (phrase, count, total))

    # ---- 优秀样本里独有的词: 值得沿用 ----
    if good:
        good_phrases = collections.Counter()
        for r in good:
            good_phrases.update(set(phrases(r["提示词"])))
        bad_phrases = set()
        for r in bad:
            bad_phrases.update(phrases(r["提示词"]))
        winners = [(p, c) for p, c in good_phrases.most_common()
                   if p not in bad_phrases and c >= 2]
        if winners:
            log("")
            log("--- 只在优秀样本里出现的词组(值得沿用) ---")
            for phrase, count in winners[:20]:
                log("  %-42s 出现 %d 次" % (phrase, count))

    if args.raw:
        log("")
        log("=" * 60)
        log("优秀样本原文")
        log("=" * 60)
        for r in good:
            log("[%s] 批次=%s seed=%s" % (r["编号"], r["批次"], r["seed"]))
            log("  %s" % r["提示词"])
        log("")
        log("=" * 60)
        log("淘汰样本原文(含原因)")
        log("=" * 60)
        for r in bad:
            log("[%s] 原因: %s" % (r["编号"], "/".join(r["淘汰原因"]) or "未填"))
            log("  %s" % r["提示词"])

    if not rated:
        log("")
        log("提示: 还没有任何评分。出图之后在飞书「评分」列标上优秀/可用/淘汰,")
        log("      淘汰的顺手勾一下原因 —— 这两列就是下一轮优化的全部燃料。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

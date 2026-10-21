# -*- coding: utf-8 -*-
"""
把套图节点从工作流里摘掉。API 格式和界面格式两份一起改。

为什么摘: 套图现在由 compose_mockups.py 做 —— 它会先按 alpha 裁掉透明边,
再按真实印刷区等比缩放定位, 比 ComfyUI 里"整张画布缩到 380x380 贴死坐标"精确得多。
留着那几个节点只会继续产出不准的图, 还白占生成时间。

摘掉之后 ComfyUI 只负责: 生图 -> 去背景 -> 存透明图和原图。
两份工作流都会先备份。
"""

import json
import os
import shutil
import sys

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
API_WORKFLOW = os.path.join(HERE, "workflow_api.json")
UI_WORKFLOW = os.path.join(paths.WORKFLOWS_DIR, "POD.json")

# 套图那一串: 缩放 / 合成 / 保存 / 加载底图。按类型+用途识别, 不写死编号。
MOCKUP_TYPES = ("ImageScale", "ImageCompositeMasked")
# 这两个是给套图用的底图加载器, 靠加载的文件名认出来
TEMPLATE_FILES = {"white.png", "black.png"}


def log(msg):
    print(msg, flush=True)


def find_mockup_nodes_api(graph):
    """API 格式: 找出套图相关的节点编号"""
    doomed = set()

    for nid, node in graph.items():
        if node.get("class_type") in MOCKUP_TYPES:
            doomed.add(nid)
        if (node.get("class_type") == "LoadImage"
                and node.get("inputs", {}).get("image") in TEMPLATE_FILES):
            doomed.add(nid)

    # 只被套图节点喂养的保存节点, 也一并摘掉
    for nid, node in graph.items():
        if node.get("class_type") != "SaveImage":
            continue
        source = node.get("inputs", {}).get("images")
        if isinstance(source, list) and str(source[0]) in doomed:
            doomed.add(nid)
    return doomed


def strip_api():
    if not os.path.exists(API_WORKFLOW):
        log("找不到 %s, 跳过" % API_WORKFLOW)
        return
    backup = API_WORKFLOW + ".withmockup.bak"
    if not os.path.exists(backup):
        shutil.copy2(API_WORKFLOW, backup)
        log("已备份 -> %s" % os.path.basename(backup))

    with open(API_WORKFLOW, "r", encoding="utf-8") as f:
        graph = json.load(f)

    doomed = find_mockup_nodes_api(graph)
    log("API 工作流: 摘掉 %d 个节点 %s"
        % (len(doomed), sorted(doomed, key=lambda x: int(x))))
    for nid in doomed:
        log("    [%s] %s" % (nid, graph[nid]["class_type"]))
        del graph[nid]

    # 确认没有残留的连线指向被删的节点
    for nid, node in graph.items():
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and str(value[0]) in doomed:
                raise SystemExit("节点 %s 的 %s 还连着被删的 %s, 停手" % (nid, key, value[0]))

    with open(API_WORKFLOW, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    log("  剩下 %d 个节点" % len(graph))
    for nid, node in sorted(graph.items(), key=lambda x: int(x[0])):
        if node["class_type"] == "SaveImage":
            log("  保留的保存节点 [%s] 前缀=%s"
                % (nid, node["inputs"].get("filename_prefix")))


def strip_ui():
    if not os.path.exists(UI_WORKFLOW):
        log("找不到界面工作流 %s, 跳过" % UI_WORKFLOW)
        return
    backup = UI_WORKFLOW + ".withmockup.bak"
    if not os.path.exists(backup):
        shutil.copy2(UI_WORKFLOW, backup)
        log("已备份 -> %s" % os.path.basename(backup))

    with open(UI_WORKFLOW, "r", encoding="utf-8") as f:
        wf = json.load(f)

    by_id = {n["id"]: n for n in wf["nodes"]}
    doomed = set()
    for node in wf["nodes"]:
        if node["type"] in MOCKUP_TYPES:
            doomed.add(node["id"])
        if node["type"] == "LoadImage" and node.get("widgets_values"):
            if node["widgets_values"][0] in TEMPLATE_FILES:
                doomed.add(node["id"])

    # 界面格式里, 保存节点的输入连线记在 links 表, 顺着找
    for link in wf["links"]:
        _, origin, _, target, _, _ = link
        if origin in doomed and by_id.get(target, {}).get("type") == "SaveImage":
            doomed.add(target)

    log("界面工作流: 摘掉 %d 个节点 %s" % (len(doomed), sorted(doomed)))
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] not in doomed]
    # 连线两端只要沾上被删节点就一起删
    wf["links"] = [l for l in wf["links"] if l[1] not in doomed and l[3] not in doomed]

    # 存活节点上残留的连线引用要清干净, 否则界面会报连线错误
    alive_links = {l[0] for l in wf["links"]}
    for node in wf["nodes"]:
        for inp in node.get("inputs") or []:
            if inp.get("link") is not None and inp["link"] not in alive_links:
                inp["link"] = None
        for out in node.get("outputs") or []:
            if out.get("links"):
                out["links"] = [x for x in out["links"] if x in alive_links]

    with open(UI_WORKFLOW, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=2)
    log("  剩下 %d 个节点, %d 条连线" % (len(wf["nodes"]), len(wf["links"])))


def main():
    strip_api()
    log("")
    strip_ui()
    log("")
    log("完成。ComfyUI 里重新打开 POD 工作流即可看到精简后的版本。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

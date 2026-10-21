# -*- coding: utf-8 -*-
"""
POD 批量出图: 读提示词表 -> 塞进 ComfyUI 队列 -> ComfyUI 自己排队跑完

用法(先确保 ComfyUI 已经开着):
    python comfy_batch.py                   # 跑 prompts.csv 里所有"待生成"的行
    python comfy_batch.py --all             # 不管状态, 全部重跑
    python comfy_batch.py --dry-run         # 只打印要跑什么, 不真的提交
    python comfy_batch.py --wait            # 提交完等它跑完再退出
    python comfy_batch.py --csv 别的表.csv   # 换一个表

只用 Python 标准库, 不需要 pip 装任何东西, 系统 python 或 conda 的都能跑。
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env_config  # noqa: E402  Load optional .env without requiring service credentials.

# ================== 【配置区】 ==================

COMFY_HOST = (os.environ.get("COMFY_HOST") or "http://127.0.0.1:8188").rstrip("/")

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_FILE = os.path.join(HERE, "workflow_api.json")
CSV_FILE = os.path.join(HERE, "prompts.csv")

# 保存节点的文件名前缀模板。左边是节点编号(对应你当前工作流), 右边支持 {id} {slug} 两个占位符。
# 以后加灰色T恤那组, 复制节点组之后, 把新的保存节点编号照样填到这里。
# 套图不在这里做了 —— compose_mockups.py 会按真实印刷区精准定位, 比工作流里
# "整张画布缩到固定尺寸贴死坐标"准得多。ComfyUI 只管出原图和透明图。
SAVE_PREFIX = {
    "9":  "raw/{id}_{slug}",            # 生图原图(不透明)
    "44": "png/{id}_{slug}",            # 去背景透明图 <- 套图和上架都用这张
}

# 提示词黄金公式(和飞书表里那条拼接公式保持一致)。
# CSV 里"完整提示词"列留空时, 才用主体/风格/配色现拼。
PROMPT_HEAD = "centered composition, "
PROMPT_MID = (
    ", solid filled shapes, bold thick outlines, clear silhouette, "
    "simple composition, flat design, "
)
PROMPT_TAIL = (
    ", isolated transparent background, clean thick outline, no complex gradient, "
    "t-shirt print design, high contrast, vector graphic, sharp edges, printable, "
    "no thin lines, no intricate details, no text, no lettering, no words"
)

# 状态列是这些值(或为空)才会跑; --all 可以忽略这个过滤
STATUS_TODO = {"", "待生成", "待生产"}

# Windows 文件名里不允许出现的字符
ILLEGAL_FILENAME_CHARS = '\\/:*?"<>|'

# ================== 【配置区】结束 ==================


def log(msg):
    print(msg, flush=True)


def load_workflow(path):
    """读 API 格式的工作流 JSON"""
    if not os.path.exists(path):
        raise SystemExit("找不到工作流文件: " + path)
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    if not isinstance(graph, dict) or "prompt" in graph or "nodes" in graph:
        raise SystemExit("工作流格式不对: 需要 API 格式(用节点编号做键), 不是界面导出的那种。")
    return graph


def find_positive_node(graph):
    """
    找正面提示词节点: 顺着采样器的 positive 输入回溯。
    这样即使以后节点编号变了也能自动找到, 不用改脚本。
    """
    for node in graph.values():
        if "KSampler" in node.get("class_type", ""):
            pos = node.get("inputs", {}).get("positive")
            if isinstance(pos, list) and pos:
                return str(pos[0])
    raise SystemExit("工作流里没找到采样器的正面提示词节点, 请检查工作流。")


def find_seed_nodes(graph):
    """所有带 seed 的节点(通常就是 K采样器)"""
    return [nid for nid, node in graph.items() if "seed" in node.get("inputs", {})]


def build_prompt(row):
    """CSV 有完整提示词就直接用; 没有就按黄金公式拼"""
    full = (row.get("完整提示词") or "").strip()
    if full:
        return full
    subject = (row.get("主体") or "").strip()
    if not subject:
        return ""
    style = (row.get("风格") or "").strip()
    palette = (row.get("配色") or "").strip()
    middle = ", ".join(x for x in (style, palette) if x)
    return PROMPT_HEAD + subject + PROMPT_MID + middle + PROMPT_TAIL


def make_slug(row, index):
    """用主体做文件名, 去掉 Windows 文件名不允许的字符"""
    raw = (row.get("主体") or row.get("系列") or "").strip()
    if not raw:
        raw = "item%d" % index
    slug = "".join("_" if (c in ILLEGAL_FILENAME_CHARS or c.isspace()) else c for c in raw)
    return slug[:40] or ("item%d" % index)


def build_job(graph, prompt_text, seed, item_id, slug):
    """复制一份工作流, 换掉提示词/种子/保存前缀, 返回可以提交的图"""
    job = json.loads(json.dumps(graph))  # 深拷贝, 每条互不干扰

    job[find_positive_node(job)]["inputs"]["text"] = prompt_text

    for nid in find_seed_nodes(job):
        job[nid]["inputs"]["seed"] = seed

    for nid, template in SAVE_PREFIX.items():
        if nid in job:
            job[nid]["inputs"]["filename_prefix"] = template.format(id=item_id, slug=slug)
        else:
            log("  ! 配置里的保存节点 %s 在工作流里不存在, 已跳过" % nid)

    return job


def submit(job):
    """提交到 ComfyUI 队列, 返回 prompt_id"""
    payload = json.dumps({"prompt": job}).encode("utf-8")
    req = urllib.request.Request(
        COMFY_HOST + "/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("prompt_id")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError("ComfyUI 拒绝了这条任务 (HTTP %s): %s" % (e.code, detail))
    except urllib.error.URLError as e:
        raise ComfyDown("连不上 ComfyUI (%s): %s。ComfyUI 开着吗?" % (COMFY_HOST, e.reason))


def queue_remaining():
    """
    还剩多少条没跑完(正在跑 + 排队中)。取不到返回 None。

    ComfyUI 加载大模型时 HTTP 服务会卡住十几秒不响应, 所以这里必须容忍超时,
    不能让一次瞬时失败掀掉整批任务。
    """
    try:
        with urllib.request.urlopen(COMFY_HOST + "/queue", timeout=60) as resp:
            q = json.loads(resp.read().decode("utf-8"))
        return len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


class ComfyDown(RuntimeError):
    """连不上 ComfyUI(没启动或已崩溃)。和"这一条跑失败了"是两回事, 必须分开处理。"""


def is_alive():
    """ComfyUI 是否在正常响应"""
    try:
        urllib.request.urlopen(COMFY_HOST + "/system_stats", timeout=20).read()
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def free_memory():
    """
    让 ComfyUI 卸载已加载的模型、释放内存。
    这台机器的模型比物理内存还大, 连着跑几条就会读失败, 每条之间清一次能稳住。
    代价是下一条要重新加载模型(慢十几秒)。
    """
    payload = json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8")
    req = urllib.request.Request(
        COMFY_HOST + "/free", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=60).read()
        return True
    except urllib.error.URLError:
        return False


def wait_until_idle(poll_seconds=5, max_blind_minutes=20):
    """
    等到队列跑空。

    查不到队列状态时(ComfyUI 正忙着加载模型, 或网络抖了一下)不算失败, 继续等;
    但连续 max_blind_minutes 分钟都问不到, 就认定它是真挂了, 抛错让上层处理。
    """
    blind_since = None
    while True:
        left = queue_remaining()
        if left is None:
            blind_since = blind_since or time.time()
            if time.time() - blind_since > max_blind_minutes * 60:
                raise RuntimeError("连续 %d 分钟问不到 ComfyUI 队列状态, 判定它已经挂了"
                                   % max_blind_minutes)
        else:
            blind_since = None
            if left == 0:
                return
        time.sleep(poll_seconds)


def fetch_history_entry(prompt_id):
    """取一条任务的执行历史; 取不到返回 None"""
    url = COMFY_HOST + "/history/" + prompt_id
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            hist = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return None
    return hist.get(prompt_id)


def fetch_outputs(prompt_id):
    """
    取每个保存节点出的图, 返回 {节点编号: [(子目录, 文件名), ...]}。
    回传飞书预览图要用到。
    """
    entry = fetch_history_entry(prompt_id)
    if not entry:
        return {}
    result = {}
    for nid, out in (entry.get("outputs") or {}).items():
        images = [(i.get("subfolder", ""), i.get("filename")) for i in out.get("images", [])]
        if images:
            result[nid] = images
    return result


def download_output(subfolder, filename):
    """从 ComfyUI 把出的图取回来(不用知道 output 目录在哪)"""
    query = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": "output"}
    )
    with urllib.request.urlopen(COMFY_HOST + "/view?" + query, timeout=60) as resp:
        return resp.read()


def fetch_result(prompt_id):
    """
    查一条任务的最终结果, 返回 (状态, 说明)。
    队列空了不等于跑成功 —— 报错的任务同样会离开队列, 所以必须回头查历史。
    """
    entry = fetch_history_entry(prompt_id)
    if not entry:
        return "未知", "查不到结果(ComfyUI 重启过?)"

    status = entry.get("status", {})
    if status.get("status_str") == "success":
        count = sum(len(o.get("images", [])) for o in entry.get("outputs", {}).values())
        return "成功", "出图 %d 张" % count

    for message in status.get("messages", []):
        if message[0] == "execution_error":
            info = message[1]
            return "失败", "节点 %s (%s): %s" % (
                info.get("node_id"),
                info.get("node_type"),
                (info.get("exception_message") or "").strip(),
            )
    return "失败", status.get("status_str") or "未知原因"


def read_rows(path):
    if not os.path.exists(path):
        raise SystemExit("找不到提示词表: " + path)
    # utf-8-sig: Excel 存的 CSV 开头有 BOM, 不处理会让第一列列名读不出来
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser(description="POD 批量出图")
    parser.add_argument("--csv", default=CSV_FILE, help="提示词表路径")
    parser.add_argument("--workflow", default=WORKFLOW_FILE, help="API 格式工作流路径")
    parser.add_argument("--all", action="store_true", help="忽略状态列, 全部重跑")
    parser.add_argument("--dry-run", action="store_true", help="只看要跑什么, 不提交")
    parser.add_argument("--wait", action="store_true", help="提交完等它跑完再退出")
    args = parser.parse_args()

    graph = load_workflow(args.workflow)
    rows = read_rows(args.csv)
    log("提示词表 %s: 共 %d 行" % (os.path.basename(args.csv), len(rows)))

    jobs = []          # [(编号, slug, prompt_id)] 用来事后核对每条到底成没成
    submitted = 0
    skipped = 0
    for index, row in enumerate(rows, start=1):
        item_id = (row.get("编号") or "%03d" % index).strip()
        status = (row.get("状态") or "").strip()

        if not args.all and status not in STATUS_TODO:
            skipped += 1
            continue

        prompt_text = build_prompt(row)
        if not prompt_text:
            log("[%s] 跳过: 完整提示词和主体都是空的" % item_id)
            skipped += 1
            continue

        seed_raw = (row.get("seed") or row.get("种子") or "").strip()
        seed = int(seed_raw) if seed_raw.isdigit() else random.randint(0, 2 ** 32 - 1)
        slug = make_slug(row, index)

        log("[%s] %s  seed=%d" % (item_id, slug, seed))
        log("        %s..." % prompt_text[:110])

        if args.dry_run:
            submitted += 1
            continue

        job = build_job(graph, prompt_text, seed, item_id, slug)
        try:
            prompt_id = submit(job)
        except RuntimeError as e:
            log("        x 提交失败: %s" % e)
            continue
        log("        √ 已入队 %s" % prompt_id)
        jobs.append((item_id, slug, prompt_id))
        submitted += 1

    log("")
    if args.dry_run:
        log("演练结束: 会提交 %d 条, 跳过 %d 条。去掉 --dry-run 就真跑。" % (submitted, skipped))
        return 0

    log("已提交 %d 条, 跳过 %d 条。ComfyUI 会自己排队跑完。" % (submitted, skipped))
    log("成品在 ComfyUI\\output\\ 下: png/=透明图, mockup/=套图, print/=300dpi印刷件, raw/=原图")

    if not (args.wait and jobs):
        return 0

    log("")
    log("等待队列跑完(Ctrl+C 可以随时退出, 不影响 ComfyUI 继续跑)...")
    try:
        while True:
            left = queue_remaining()
            if left == 0:
                break
            log("  还剩 %s 条..." % ("?" if left is None else left))
            time.sleep(10)
    except KeyboardInterrupt:
        log("已退出等待, ComfyUI 那边还在继续跑。")
        return 0
    except RuntimeError as e:
        log("  × %s" % e)
        return 1

    log("")
    log("===== 结果 =====")
    failed = []
    for item_id, slug, prompt_id in jobs:
        state, detail = fetch_result(prompt_id)
        log("[%s] %s  %s  %s" % (item_id, slug, state, detail))
        if state != "成功":
            failed.append((item_id, detail))

    log("")
    if failed:
        log("%d 条成功, %d 条失败:" % (len(jobs) - len(failed), len(failed)))
        for item_id, detail in failed:
            log("  [%s] %s" % (item_id, detail))
        log("")
        log("提示: 报 hostbuf_file_reader_read failed 是内存不够读不动大模型,")
        log("      重启 ComfyUI 释放内存后重跑这几条即可。")
        return 1
    log("全部 %d 条成功。" % len(jobs))
    return 0


if __name__ == "__main__":
    # Windows 控制台默认不是 UTF-8, 不处理中文会乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

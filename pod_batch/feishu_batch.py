# -*- coding: utf-8 -*-
"""
POD 批量出图(飞书版): 直接读飞书提示词库表 -> 塞进 ComfyUI 队列 -> 跑完回写状态

和 comfy_batch.py 的区别只在提示词来源: 那个读 CSV, 这个读飞书表。
提交/出图/查结果这些逻辑是同一套(直接复用 comfy_batch 里的函数)。

用法(先确保 ComfyUI 已经开着):
    python feishu_batch.py --dry-run    # 只看飞书表里有哪些要跑, 不提交也不回写
    python feishu_batch.py              # 跑"待生成"的行, 跑完把状态改成"已生成"
    python feishu_batch.py --all        # 忽略状态列, 全部重跑
    python feishu_batch.py --no-writeback   # 跑但不改飞书的状态

凭证从上一级目录的 .env 读(和抠图服务共用一套), 代码里没有明文。
"""

import argparse
import collections
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

# 复用上一级目录的凭证读取, 和 comfy_batch 的提交逻辑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env_config import APP_ID, APP_SECRET, APP_TOKEN  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_batch      # noqa: E402
import compose_mockups  # noqa: E402
import paths            # noqa: E402

# ================== 【配置区】 ==================

# 提示词库表(和抠图工作台在同一个 base, 但是不同的表)
PROMPT_TABLE_ID = os.environ.get("FEISHU_PROMPT_TABLE_ID", "EXAMPLE_FEISHU_ID")

# 国内飞书。用国际版 Lark 的话改成 https://open.larksuite.com
FEISHU_HOST = "https://open.feishu.cn"

# 表里的列名(和飞书表保持一致)
F_ID = "编号"
F_SUBJECT = "主体"
F_STYLE = "风格"
F_PALETTE = "配色"
F_PROMPT = "完整提示词"
F_STATUS = "状态"
F_SEED = "seed"
F_PREVIEW = "预览图"
F_TIME = "生成时间"
F_FILENAME = "出图文件名"

# 状态值(和飞书单选的选项文字完全一致)
ST_TODO = "待生成"
ST_DOING = "生成中"
ST_DONE = "已生成"
ST_FAILED = "生成失败"
# 注意"生成中"不在这里: 那是别的机器正在跑的记录, 不能抢
STATUS_TODO = {"", ST_TODO, ST_FAILED}

# 工作流里出透明图的保存节点(和 comfy_batch.SAVE_PREFIX 对应)。
# 套图已经从工作流里摘掉了, 改由 compose_mockups 按真实印刷区精准合成,
# 飞书预览图也用那张 —— 表里看到的就是实际会印出来的样子。
NODE_PNG = "44"
PREVIEW_TEMPLATE = "white_front"   # 用哪张套图当飞书预览图(底图_印刷位)

# 风格中文选项 -> 英文提示词。
# 飞书里"风格"是中文单选, 而 FLUX 只认英文, 直接拼中文进提示词是不起作用的,
# 所以在这里翻译。以后飞书加了新的风格选项, 在这里补一行即可。
STYLE_MAP = {
    "kawaii扁平": "kawaii cute style, cartoon flat illustration",
    "贴纸": "bold sticker design",
    "卡通扁平": "cartoon flat illustration",
    "波普": "pop art style",
    "复古吉祥物": "retro mascot logo",
}

# 配色中文 -> 英文。配色列是自由文本, 填英文就直接用, 填中文才走这个表。
PALETTE_MAP = {
    "明亮": "bright cheerful colors",
    "马卡龙": "pastel colors",
    "复古橙棕": "70s retro orange and brown palette",
    "黑白": "black and white bold",
    "大地色": "muted earth tones",
}

# ================== 【配置区】结束 ==================


Job = collections.namedtuple("Job", "item_id slug prompt_id record_id seed prompt_text")


def log(msg):
    print(msg, flush=True)


def has_chinese(text):
    return any("一" <= c <= "鿿" for c in text)


def request_json(url, token=None, body=None, method=None, tries=3):
    """带重试的飞书请求。飞书偶发超时, 一次失败就退出太脆了。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token

    last_error = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=40) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") != 0:
                raise RuntimeError("飞书返回错误 code=%s msg=%s" % (result.get("code"), result.get("msg")))
            return result
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < tries - 1:
                log("  网络不稳, 重试 %d/%d ..." % (attempt + 1, tries - 1))
                time.sleep(3)
    raise RuntimeError("请求飞书失败: %s" % last_error)


def get_token():
    result = request_json(
        FEISHU_HOST + "/open-apis/auth/v3/tenant_access_token/internal",
        body={"app_id": APP_ID, "app_secret": APP_SECRET},
    )
    return result["tenant_access_token"]


def field_text(value):
    """
    飞书字段值形状不统一: 文本是字符串, 公式/富文本是 [{'text': ...}], 单选是字符串。
    统一转成纯文本。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("name") or "")
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return (value.get("text") or value.get("name") or "").strip()
    return str(value).strip()


def list_records(token):
    """读提示词表全部记录(自动翻页)"""
    records = []
    page_token = None
    while True:
        url = "%s/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=100" % (
            FEISHU_HOST, APP_TOKEN, PROMPT_TABLE_ID,
        )
        if page_token:
            url += "&page_token=" + page_token
        data = request_json(url, token=token)["data"]
        records.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return records


def translate(value, mapping):
    """中文选项翻译成英文; 本来就是英文的原样返回"""
    if not value:
        return ""
    if value in mapping:
        return mapping[value]
    if has_chinese(value):
        log("    ! 「%s」没有对应的英文翻译, 已跳过这个词(去脚本 STYLE_MAP/PALETTE_MAP 里补)" % value)
        return ""
    return value


# 这些开头是纯构图词, 不说明画的是什么, 取文件名时跳过
GENERIC_OPENERS = ("centered composition", "single character centered",
                   "circular badge layout", "isolated", "flat design")


def make_slug(subject, prompt_text, item_id):
    """
    出图文件名。优先用"主体"; 主体空着(自由提示词的行)就取提示词里第一段
    真正描述画面的内容 —— 跳过纯构图词, 这样文件名看得懂画的是什么。
    """
    source = subject
    if not source:
        parts = [p.strip() for p in prompt_text.split(",") if p.strip()]
        for part in parts:
            if not part.lower().startswith(GENERIC_OPENERS):
                source = part
                break
        if not source and parts:
            source = parts[0]
    if not source:
        return "item%s" % item_id
    # 截到 40 字符, 但不切断单词
    words = source.split()
    trimmed = ""
    for word in words:
        if len(trimmed) + len(word) + 1 > 40:
            break
        trimmed = (trimmed + " " + word).strip()
    return comfy_batch.make_slug({"主体": trimmed or source}, 0)


def build_prompt(subject, style, palette):
    """
    按黄金公式拼提示词。
    这里不直接用飞书"完整提示词"那一列 —— 那条公式会把中文的风格原样拼进去,
    而 FLUX 只认英文, 拼中文等于白填。所以在脚本里翻译后重新拼。
    """
    middle = ", ".join(x for x in (style, palette) if x)
    return (
        comfy_batch.PROMPT_HEAD + subject + comfy_batch.PROMPT_MID + middle + comfy_batch.PROMPT_TAIL
    )


def update_record(token, record_id, fields):
    """回写一条记录的若干列"""
    url = "%s/open-apis/bitable/v1/apps/%s/tables/%s/records/%s" % (
        FEISHU_HOST, APP_TOKEN, PROMPT_TABLE_ID, record_id,
    )
    request_json(url, token=token, body={"fields": fields}, method="PUT")


def upload_attachment(token, filename, content):
    """
    把图片传成多维表格附件, 返回 file_token。
    飞书这个接口只收 multipart/form-data, 标准库没有现成的编码器, 手工拼。
    """
    boundary = "----podbatch%d" % int(time.time() * 1000)
    sep = ("--" + boundary).encode()
    parts = []
    for key, value in (
        ("file_name", filename),
        ("parent_type", "bitable_file"),
        ("parent_node", APP_TOKEN),
        ("size", str(len(content))),
    ):
        parts.append(sep)
        parts.append(('Content-Disposition: form-data; name="%s"' % key).encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    parts.append(sep)
    parts.append(
        ('Content-Disposition: form-data; name="file"; filename="%s"' % filename).encode("utf-8")
    )
    parts.append(b"Content-Type: image/png")
    parts.append(b"")
    parts.append(content)
    parts.append(("--" + boundary + "--").encode())
    body = b"\r\n".join(parts)

    req = urllib.request.Request(
        FEISHU_HOST + "/open-apis/drive/v1/medias/upload_all",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("code") != 0:
        raise RuntimeError("上传附件失败 code=%s msg=%s" % (result.get("code"), result.get("msg")))
    return result["data"]["file_token"]


def build_writeback(token, prompt_id, seed):
    """
    组装跑成功后要回写的内容: 状态/seed/时间/出图文件名/预览图。
    预览图上传失败不影响其他列 —— 图已经在本地出好了, 不该因为传不上去就把整条标成失败。
    """
    fields = {
        F_STATUS: ST_DONE,
        F_SEED: str(seed),
        F_TIME: int(time.time() * 1000),   # 飞书日期字段收毫秒时间戳
    }
    outputs = comfy_batch.fetch_outputs(prompt_id)
    png = outputs.get(NODE_PNG) or []
    if not png:
        log("        ! 没找到透明图输出, 跳过套图和预览图")
        return fields

    subfolder, filename = png[0]
    fields[F_FILENAME] = filename

    # 套图 + 印刷件都在这一步产出, 和批量跑 compose_mockups.py 是同一套逻辑
    png_path = os.path.join(paths.COMFY_ROOT, "output", subfolder, filename)
    try:
        mockups = compose_mockups.compose_one(png_path)
    except Exception as e:
        log("        ! 套图失败(透明图已经出了): %s" % e)
        return fields

    preview_path = mockups.get(PREVIEW_TEMPLATE)
    if not preview_path:
        return fields
    try:
        with open(preview_path, "rb") as f:
            content = f.read()
        file_token = upload_attachment(token, os.path.basename(preview_path), content)
        fields[F_PREVIEW] = [{"file_token": file_token}]
    except Exception as e:
        log("        ! 预览图回传失败(套图已存到本地): %s" % e)

    return fields


def main():
    parser = argparse.ArgumentParser(description="POD 批量出图(读飞书提示词库)")
    parser.add_argument("--workflow", default=comfy_batch.WORKFLOW_FILE, help="API 格式工作流路径")
    parser.add_argument("--all", action="store_true", help="忽略状态列, 全部重跑")
    parser.add_argument("--dry-run", action="store_true", help="只看要跑什么, 不提交不回写")
    parser.add_argument("--no-writeback", action="store_true", help="跑完不改飞书状态")
    parser.add_argument("--queue-all", action="store_true",
                        help="一次全部入队(默认是逐条跑, 每条跑完立刻回写)")
    parser.add_argument("--free-between", action="store_true",
                        help="每条之间卸载模型腾内存。换成 GGUF 量化模型后一般不需要了, "
                             "只有报 read_file_slice failed 才加上")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几条(0=不限)")
    parser.add_argument("--reset-stuck", action="store_true",
                        help="把卡在「生成中」的记录改回「待生成」(脚本被强行中断后用)")
    args = parser.parse_args()

    if args.reset_stuck:
        return reset_stuck(get_token())

    graph = comfy_batch.load_workflow(args.workflow)

    if not args.dry_run and not comfy_batch.is_alive():
        log("连不上 ComfyUI (%s)。" % comfy_batch.COMFY_HOST)
        log("先把 ComfyUI 打开再跑本脚本。飞书表里一个字都没动。")
        return 1

    log("连接飞书 ...")
    token = get_token()
    records = list_records(token)
    log("提示词库表: 共 %d 条记录" % len(records))
    log("")

    plan = []
    skipped = 0
    for record in records:
        fields = record.get("fields") or {}
        item_id = field_text(fields.get(F_ID)) or record["record_id"][-4:]
        status = field_text(fields.get(F_STATUS))

        if not args.all and status not in STATUS_TODO:
            skipped += 1
            continue

        # 现在「完整提示词」是普通文本, 智能体直接往里写整条提示词, 这是主路径。
        # 只填了主体没填提示词的老式行, 才退回三段拼接。
        subject = field_text(fields.get(F_SUBJECT))
        prompt_text = field_text(fields.get(F_PROMPT))

        if not prompt_text:
            if not subject:
                log("[%s] 跳过: 完整提示词和主体都是空的" % item_id)
                skipped += 1
                continue
            style = translate(field_text(fields.get(F_STYLE)), STYLE_MAP)
            palette = translate(field_text(fields.get(F_PALETTE)), PALETTE_MAP)
            prompt_text = build_prompt(subject, style, palette)
        elif has_chinese(prompt_text):
            log("[%s] 跳过: 提示词里有中文, FLUX 只认英文 -> %s"
                % (item_id, "".join(c for c in prompt_text if "一" <= c <= "鿿")[:20]))
            skipped += 1
            continue

        # seed 留空就随机; 填了就照着复现(好图固定 seed, 换主体裂变)
        seed_raw = field_text(fields.get(F_SEED))
        seed = int(seed_raw) if seed_raw.isdigit() else random.randint(0, 2 ** 32 - 1)
        slug = make_slug(subject, prompt_text, item_id)

        log("[%s] %s  seed=%d" % (item_id, slug, seed))
        log("        %s..." % prompt_text[:110])

        plan.append(Job(item_id, slug, None, record["record_id"], seed, prompt_text))
        if args.limit and len(plan) >= args.limit:
            log("(--limit %d 已达上限, 后面的先不跑)" % args.limit)
            break

    log("")
    if args.dry_run:
        log("演练结束: 会跑 %d 条, 跳过 %d 条。去掉 --dry-run 就真跑。" % (len(plan), skipped))
        return 0

    if not plan:
        log("没有要跑的记录(跳过 %d 条)。" % skipped)
        return 0

    log("要跑 %d 条, 跳过 %d 条。" % (len(plan), skipped))
    if args.queue_all:
        log("模式: 一次全部入队(快, 但这台机器内存吃紧时容易整批失败)")
    else:
        log("模式: 逐条跑, 每条之间卸载模型腾内存(慢一点, 但稳, 且每条跑完立刻回写)")
    log("")

    try:
        done, failed = run_plan(args, token, graph, plan)
    except comfy_batch.ComfyDown as e:
        log("")
        log("!! %s" % e)
        log("   已经中断, 没跑到的记录仍是「%s」, ComfyUI 起来后重跑即可。" % ST_TODO)
        # 被认领成"生成中"但没跑的, 放回待生成, 别让它们卡住
        reset_stuck(token)
        return 1

    log("")
    if failed:
        log("%d 条成功, %d 条失败:" % (done, len(failed)))
        for item_id, detail in failed:
            log("  [%s] %s" % (item_id, detail))
        log("")
        log("提示: read_file_slice / hostbuf_file_reader_read failed = 内存装不下这个模型。")
        log("      失败的记录已标「%s」, 直接重跑本脚本会自动重来。" % ST_FAILED)
        return 1

    log("全部 %d 条成功。%s" % (done, "(--no-writeback: 飞书没动)" if args.no_writeback else "飞书已回写。"))
    log("成品在 ComfyUI\\output\\ 下: png/=透明图, mockup/=套图, print/=300dpi印刷件, raw/=原图")
    return 0


def run_plan(args, token, graph, plan):
    """
    依次跑完计划里的每一条, 返回 (成功条数, [(编号, 失败原因)])。

    默认逐条跑: 提交 -> 等它跑完 -> 回写 -> 卸载模型 -> 下一条。
    这台机器的模型(16.05GiB)比物理内存(15.8GiB)还大, 连着跑必然读失败,
    每条之间清一次内存是目前唯一能稳住的办法。
    --queue-all 是原来的一次性全入队, 内存够的机器上更快。
    """
    done = 0
    failed = []

    if args.queue_all:
        submitted = []
        for job in plan:
            claim(args, token, job)
            prompt_id = try_submit(graph, job)
            if prompt_id:
                submitted.append(job._replace(prompt_id=prompt_id))
        comfy_batch.wait_until_idle()
        for job in submitted:
            log("[%s] %s" % (job.item_id, job.slug))
            if settle(args, token, job):
                done += 1
            else:
                failed.append((job.item_id, last_detail[0]))
        return done, failed

    for index, job in enumerate(plan, start=1):
        log("[%s] %s  (%d/%d)  seed=%d" % (job.item_id, job.slug, index, len(plan), job.seed))
        claim(args, token, job)
        prompt_id = try_submit(graph, job)
        if not prompt_id:
            failed.append((job.item_id, "提交失败"))
            continue
        try:
            comfy_batch.wait_until_idle()
        except KeyboardInterrupt:
            log("  已中断。已经跑完的都回写了, 剩下的还在「待生成」, 下次接着跑。")
            break
        if settle(args, token, job._replace(prompt_id=prompt_id)):
            done += 1
        else:
            failed.append((job.item_id, last_detail[0]))
        if args.free_between:
            comfy_batch.free_memory()

    return done, failed


# settle() 要把失败原因带出来给调用方, 用一个单元素列表当传递媒介
last_detail = [""]


def reset_stuck(token):
    """
    脚本被 Ctrl+C 或断电中断时, 已认领但没跑完的记录会卡在"生成中",
    之后谁都不会再碰它。这个开关把它们放回"待生成"。
    注意: 另一台机器正在跑的记录也是"生成中", 别在它跑着的时候用这个。
    """
    stuck = [r for r in list_records(token)
             if field_text((r.get("fields") or {}).get(F_STATUS)) == ST_DOING]
    if not stuck:
        log("没有卡在「%s」的记录。" % ST_DOING)
        return 0
    log("发现 %d 条卡在「%s」:" % (len(stuck), ST_DOING))
    for record in stuck:
        fields = record.get("fields") or {}
        item_id = field_text(fields.get(F_ID))
        log("  [%s] %s -> %s" % (item_id, field_text(fields.get(F_SUBJECT)), ST_TODO))
        update_record(token, record["record_id"], {F_STATUS: ST_TODO})
    log("已全部改回「%s」。" % ST_TODO)
    return 0


def claim(args, token, job):
    """
    把记录先标成"生成中"再开跑。
    多台机器共用同一张表时, 这一步保证别人不会把同一条也抢去跑一遍
    ("生成中"不在 STATUS_TODO 里)。和抠图服务里"处理中"是同一个套路。
    """
    if args.no_writeback:
        return
    try:
        update_record(token, job.record_id, {F_STATUS: ST_DOING})
    except RuntimeError as e:
        log("        ! 标记生成中失败(继续跑): %s" % e)


def try_submit(graph, job):
    """
    提交一条, 成功返回 prompt_id, 失败返回 None。
    ComfyUI 整个挂掉是另一回事 —— 直接往上抛, 让调用方立刻停下,
    而不是把剩下几十条统统标成"生成失败"(它们根本没被真正尝试过)。
    """
    built = comfy_batch.build_job(graph, job.prompt_text, job.seed, job.item_id, job.slug)
    try:
        return comfy_batch.submit(built)
    except comfy_batch.ComfyDown:
        raise
    except RuntimeError as e:
        log("        x 提交失败: %s" % e)
        return None


def settle(args, token, job):
    """查一条的结果并回写飞书。成功返回 True"""
    state, detail = comfy_batch.fetch_result(job.prompt_id)
    last_detail[0] = detail
    log("        %s  %s" % (state, detail))

    if state != "成功":
        if not args.no_writeback:
            # 标成"生成失败"让它在表里看得见; 它仍属于待跑状态, 下次会自动重来
            try:
                update_record(token, job.record_id, {F_STATUS: ST_FAILED})
            except RuntimeError as e:
                log("        ! 状态回写失败: %s" % e)
        return False

    if args.no_writeback:
        return True
    try:
        update_record(token, job.record_id, build_writeback(token, job.prompt_id, job.seed))
        log("        → 已回写飞书(状态/seed/时间/文件名/预览图)")
    except RuntimeError as e:
        log("        ! 回写失败(图已经出了, 在本地): %s" % e)
    return True


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

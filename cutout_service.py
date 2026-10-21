# -*- coding: utf-8 -*-
"""
飞书图案抠图常驻服务
流程: 轮询多维表格 -> 发现"待处理"新图 -> 下载 -> 超分 -> 抠图 -> 回写成品 -> 状态改"待审核" -> 通知审核人
适配: Windows + RTX 5060 Ti, rembg(CPU) + Real-ESRGAN(ncnn-vulkan, GPU)

运行前必须先填好下面【配置区】的所有项。
"""

import os
import sys
import json
import time
import shutil
import logging
import subprocess
from datetime import datetime

from env_config import APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID, REVIEWER_OPEN_IDS

import numpy as np
import cv2

import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
from lark_oapi.api.im.v1 import *
from rembg import remove, new_session

# ================== 【配置区】开始 —— 这些必须改成你自己的 ==================

# --- 飞书自建应用凭证(在飞书开放平台 - 你的应用 - 凭证与基础信息 里找)---
# APP_ID is loaded from local .env.
# APP_SECRET is loaded from local .env.

# --- 多维表格定位(从多维表格的网页地址里取)---
# 地址形如: https://example.feishu.cn/base/【APP_TOKEN】?table=【TABLE_ID】&view=...
# APP_TOKEN is loaded from local .env.
# TABLE_ID is loaded from local .env.

# --- 审核人(填他们的 open_id, 处理完会 @ 通知这几个人)---
# open_id 怎么拿: 见配套文档"如何获取审核人 open_id"一节
# REVIEWER_OPEN_IDS is loaded from local .env.

# --- Real-ESRGAN 超分程序路径 + 模型 ---
REALESRGAN_EXE = os.environ.get("REALESRGAN_EXE") or "realesrgan-ncnn-vulkan.exe"
REALESRGAN_MODEL = "realesrgan-x4plus"   # 插画风可改 "realesrgan-x4plus-anime"
REALESRGAN_SCALE = "4"                   # 单次放大倍数(内部用, 一般不用改)

# --- 画质档位: 员工在表格选 4K/6K, 决定超分后图片的长边像素 ---
F_QUALITY = "画质"                        # 多维表格单选列名
QUALITY_MAP = {
    "4K": 3840,      # 长边 3840 像素
    "6K": 5760,      # 长边 5760 像素
}
QUALITY_DEFAULT = 3840                    # 员工没选时默认 4K

# --- 超分后锐化强度(让内部线条/轮廓更清晰; 0=不锐化, 越大越锐)---
SR_SHARPEN = 0.6                          # 建议 0.4~0.9

# --- 超分分块大小(防止6K等大图爆显存; 显存够可设0=不分块, 报显存不足就调小如128)---
SR_TILE = 200

# --- 边缘清晰化强度: 0=不处理 1=轻度 2=干净硬边 3=纯硬边 ---
EDGE_MODE = 1                             # 你选的: 轻度

# --- 去黑边(黑底图案抠图后, 去掉外轮廓的黑边残留)---
DEBLACK_ON = True                         # 是否开启去黑边
DEBLACK_COLOR_TH = 10                     # 彩色占比阈值(%): 低于此=黑白图(全去黑), 高于=彩色图(只去外边)
DEBLACK_BAND = 6                          # 彩色图去外边的宽度(像素), 中等档
DEBLACK_DARK_TH = 110                     # 多暗算黑(0黑~255白), 越大去得越多
DEBLACK_ITERS = 3                         # 外边剥离迭代次数
DEBLACK_KILL_SEMI = True                  # 消除边缘半透明黑带(黑主体图如狼图必需)

# --- 处理模式(表格单选列): 标准 / 保守留全 ---
F_MODE = "处理模式"                        # 多维表格单选列名
MODE_CONSERVATIVE = "保守留全"             # 选这个=开alpha matting+跳过删除性处理, 尽量多保留交人工
# 其他值或不选 => 标准模式

# --- rembg 抠图模型 ---
REMBG_MODEL = "isnet-general-use"        # 快; 想要最高质量可改 "birefnet-general"

# --- 员工可在表格里选模型: 中文选项 -> 实际模型名 的映射 ---
# 超分模型下拉列名 = F_SR_MODEL, 选项文字对应下面 map 的 key
F_SR_MODEL = "超分模型"                   # 多维表格单选列名
SR_MODEL_MAP = {
    "真实照片": "realesrgan-x4plus",
    "插画卡通": "realesrgan-x4plus-anime",
}
SR_MODEL_DEFAULT = "realesrgan-x4plus-anime"   # 员工没选时用这个(你的图多是插画, 默认 anime)

# 抠图模型下拉列名 = F_CUT_MODEL
F_CUT_MODEL = "抠图模型"                   # 多维表格单选列名
CUT_MODEL_MAP = {
    "快速": "isnet-general-use",
    "高质量": "birefnet-general",
}
CUT_MODEL_DEFAULT = "birefnet-general"    # 员工没选时用这个(默认高质量)

# --- 工作目录(存放中间文件, 用纯英文无空格路径)---
WORK_DIR = os.environ.get("POD_WORK_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "work")

# --- 多维表格里的字段名(必须和你建表时的列名完全一致)---
F_STATUS = "状态"       # 单选: 待处理/处理中/待审核/已通过/已驳回/处理失败
F_SRC    = "原图"       # 附件列: 员工上传裁好的图案图
F_OUT    = "成品图"     # 附件列: 服务回写的透明成品
F_ERR    = "错误信息"   # 文本列: 出错时写原因

# --- 状态值(和上面单选选项文字一致)---
ST_PENDING = "待处理"
ST_DOING   = "处理中"
ST_REVIEW  = "待审核"
ST_FAILED  = "处理失败"

# --- 轮询间隔(秒)---
POLL_INTERVAL = 15

# ================== 【配置区】结束 ==================


# ---------- 日志: 同时输出到控制台和文件 ----------
os.makedirs(WORK_DIR, exist_ok=True)
LOG_FILE = os.path.join(WORK_DIR, "service.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cutout")

# ---------- 初始化飞书客户端 + rembg 会话 ----------
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).domain(lark.FEISHU_DOMAIN).build()

# rembg 会话按模型名缓存, 用到哪个才加载哪个(首次用会下载对应模型)
_rembg_sessions = {}
def get_rembg_session(model_name):
    if model_name not in _rembg_sessions:
        log.info("正在加载 rembg 模型: %s ...", model_name)
        _rembg_sessions[model_name] = new_session(model_name)
        log.info("rembg 模型 %s 加载完成", model_name)
    return _rembg_sessions[model_name]


def list_pending_records():
    """查出所有 状态=待处理 且 原图不为空 的记录"""
    req = SearchAppTableRecordRequest.builder() \
        .app_token(APP_TOKEN).table_id(TABLE_ID) \
        .request_body(
            SearchAppTableRecordRequestBody.builder()
            .filter(
                FilterInfo.builder()
                .conjunction("and")
                .conditions([
                    Condition.builder()
                    .field_name(F_STATUS).operator("is").value([ST_PENDING]).build()
                ]).build()
            ).build()
        ).build()
    resp = client.bitable.v1.app_table_record.search(req)
    if not resp.success():
        log.error("查询记录失败: code=%s msg=%s", resp.code, resp.msg)
        return []
    items = resp.data.items or []
    return items


def download_src(record):
    """下载记录里"原图"附件的第一张, 返回本地路径"""
    fields = record.fields or {}
    atts = fields.get(F_SRC)
    if not atts:
        raise RuntimeError("原图列为空")
    att = atts[0]
    file_token = att.get("file_token")
    name = att.get("name", "src.png")
    if not file_token:
        raise RuntimeError("原图附件没有 file_token")

    req = DownloadMediaRequest.builder().file_token(file_token).build()
    resp = client.drive.v1.media.download(req)
    if not resp.success():
        raise RuntimeError(f"下载原图失败: code={resp.code} msg={resp.msg}")

    ext = os.path.splitext(name)[1] or ".png"
    src_path = os.path.join(WORK_DIR, f"src_{record.record_id}{ext}")
    with open(src_path, "wb") as f:
        f.write(resp.file.read())
    log.info("已下载原图 -> %s", src_path)
    return src_path


def run_superres(src_path, record_id, sr_model, target_long):
    """
    超分阶段: 把图放大并锐化到目标 K 数(target_long = 目标长边像素)。
    做法: Real-ESRGAN 反复整数倍放大, 直到长边 >= 目标, 再高质量降采样到精确目标尺寸,
          最后做一次锐化(unsharp), 让内部线条和外部轮廓更清晰。
    """
    cur_path = src_path
    step = 0
    # 读当前长边
    def long_edge(p):
        im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        return max(im.shape[:2]), im

    cur_long, _ = long_edge(cur_path)

    # 若原图已经 >= 目标, 不需要 AI 放大, 直接进入后面的缩放/锐化
    if cur_long >= target_long:
        log.info("原图长边 %d 已达标(目标 %d), 跳过AI放大", cur_long, target_long)
    else:
        # 只需放大一次即可覆盖目标时: 先把原图预缩到"放大一次刚好略超目标"的尺寸,
        # 避免大原图放大后冲到巨大中间尺寸而爆显存。
        scale_factor = int(REALESRGAN_SCALE)     # 单次放大倍数(4)
        # 预期放大一次后的长边; 若已足够, 先把输入下采样到 目标/倍数 稍大一点
        pre_target = int(target_long / scale_factor * 1.05)
        if cur_long > pre_target:
            img0 = cv2.imdecode(np.fromfile(cur_path, np.uint8), cv2.IMREAD_COLOR)
            s = pre_target / max(img0.shape[:2])
            img0 = cv2.resize(img0, (round(img0.shape[1]*s), round(img0.shape[0]*s)),
                              interpolation=cv2.INTER_AREA)
            pre_path = os.path.join(WORK_DIR, f"sr_{record_id}_pre.png")
            cv2.imencode(".png", img0)[1].tofile(pre_path)
            cur_path = pre_path
            cur_long = max(img0.shape[:2])
            log.info("原图较大, 预缩到长边 %d 再放大, 防止爆显存", cur_long)

        # 反复整数倍放大直到 >= 目标(通常一次即可)
        while cur_long < target_long and step < 3:
            step += 1
            up_path = os.path.join(WORK_DIR, f"sr_{record_id}_up{step}.png")
            cmd = [REALESRGAN_EXE, "-i", cur_path, "-o", up_path,
                   "-n", sr_model, "-s", REALESRGAN_SCALE,
                   "-t", str(SR_TILE)]        # 分块处理, 防爆显存
            log.info("超分放大第%d次: %s", step, " ".join(cmd))
            r = subprocess.run(cmd, cwd=os.path.dirname(REALESRGAN_EXE),
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(up_path):
                raise RuntimeError(f"超分失败: {r.stderr[-500:]}")
            cur_path = up_path
            cur_long, _ = long_edge(cur_path)

    # 高质量缩放到精确目标长边(放大够了就是降采样, 线条更实)
    img = cv2.imdecode(np.fromfile(cur_path, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    scale = target_long / max(h, w)
    new_w, new_h = round(w * scale), round(h * scale)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    img = cv2.resize(img, (new_w, new_h), interpolation=interp)

    # 锐化: unsharp mask, 让内部线条/轮廓更清晰
    if SR_SHARPEN > 0:
        blur = cv2.GaussianBlur(img, (0, 0), 1.2)
        img = cv2.addWeighted(img, 1 + SR_SHARPEN, blur, -SR_SHARPEN, 0)
        img = np.clip(img, 0, 255).astype(np.uint8)

    out_path = os.path.join(WORK_DIR, f"sr_{record_id}.png")
    cv2.imencode(".png", img)[1].tofile(out_path)
    log.info("超分完成 -> %s (%dx%d)", out_path, new_w, new_h)
    return out_path


def run_cutout(sr_path, record_id, cut_model, conservative=False):
    """
    调 rembg 抠图, 返回透明成品路径。
    - 标准模式: rembg 抠图 + 边缘清晰化 + 去黑边
    - 保守留全模式(conservative=True): 开 alpha matting 尽量保住细线/发丝,
      跳过边缘清晰化和去黑边, 尽量多保留, 交给人工精修(适合心电图线、毛发等精细图)
    """
    out_path = os.path.join(WORK_DIR, f"out_{record_id}.png")
    with open(sr_path, "rb") as f:
        data = f.read()
    session = get_rembg_session(cut_model)
    if conservative:
        # alpha matting 更好保留边缘细结构; 前景阈值调低=更宽容, 少删
        cut_bytes = remove(data, session=session,
                           alpha_matting=True,
                           alpha_matting_foreground_threshold=240,
                           alpha_matting_background_threshold=10,
                           alpha_matting_erode_size=0)
    else:
        cut_bytes = remove(data, session=session)

    cut_arr = cv2.imdecode(np.frombuffer(cut_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if cut_arr.ndim == 3 and cut_arr.shape[2] == 4:
        alpha = cut_arr[:, :, 3]
    else:
        alpha = np.full(cut_arr.shape[:2], 255, np.uint8)
    sr_bgr = cv2.imdecode(np.fromfile(sr_path, np.uint8), cv2.IMREAD_COLOR)
    h, w = alpha.shape
    if sr_bgr.shape[:2] != (h, w):
        sr_bgr = cv2.resize(sr_bgr, (w, h), interpolation=cv2.INTER_AREA)

    if conservative:
        # 保守模式: 不做任何删除性处理, 尽量多保留
        log.info("抠图(保守留全模式): 跳过边缘清晰化与去黑边, 尽量多保留")
    else:
        # 标准模式: 边缘清晰化 + 去黑边
        alpha = sharpen_edge(alpha, EDGE_MODE)
        if DEBLACK_ON:
            alpha = remove_black_edge(sr_bgr, alpha)

    bgra = np.dstack([sr_bgr, alpha])
    cv2.imencode(".png", bgra)[1].tofile(out_path)
    log.info("抠图完成 -> %s", out_path)
    return out_path


def remove_black_edge(bgr, alpha):
    """
    去黑边(自动判断图案类型):
    - 彩色占比 < DEBLACK_COLOR_TH% => 黑白图案: 去掉所有黑色
    - 否则 => 彩色图案: 只去外轮廓黑边(内部黑保留, 不伤图案)
    参数在配置区: DEBLACK_COLOR_TH / DEBLACK_BAND / DEBLACK_DARK_TH / DEBLACK_ITERS
    """
    a = alpha.copy()
    opaque = a > 128
    if opaque.sum() == 0:
        return a
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    gray = bgr.mean(axis=2)
    colored_ratio = (opaque & (sat > 50)).sum() / opaque.sum() * 100

    if colored_ratio < DEBLACK_COLOR_TH:
        # 黑白图案: 所有暗像素变透明
        a[(a > 0) & (gray < DEBLACK_DARK_TH)] = 0
        log.info("去黑边: 判定黑白图(彩色%.1f%%), 全去黑", colored_ratio)
    else:
        # 彩色图案: 只剥离外轮廓的暗色像素, 内部黑保留
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        for _ in range(DEBLACK_ITERS):
            op = (a > 128).astype(np.uint8)
            er = cv2.erode(op, k, iterations=DEBLACK_BAND)
            edge = (op > 0) & (er == 0)
            a[edge & (gray < DEBLACK_DARK_TH)] = 0
        log.info("去黑边: 判定彩色图(彩色%.1f%%), 只去外轮廓黑边", colored_ratio)

    # 消除边缘"半透明黑带": 半透明且暗的像素直接变透明(黑主体图边缘尤其需要)
    if DEBLACK_KILL_SEMI:
        semi_dark = (a > 0) & (a < 235) & (gray < DEBLACK_DARK_TH)
        a[semi_dark] = 0
    return a


def sharpen_edge(a, mode):
    """边缘清晰化: 0=不处理 1=轻度 2=干净硬边 3=纯硬边"""
    a = a.astype(np.float32)
    if mode == 0:
        return a.astype(np.uint8)
    if mode == 1:   # 轻度: 陡峭化过渡带, 保留一点抗锯齿
        return np.clip((a - 128) * 1.8 + 128, 0, 255).astype(np.uint8)
    if mode == 2:   # 干净硬边: 阈值 + 极轻羽化防锯齿
        b = np.where(a > 128, 255, 0).astype(np.uint8)
        return cv2.GaussianBlur(b, (0, 0), 0.7)
    if mode == 3:   # 纯硬边
        return np.where(a > 128, 255, 0).astype(np.uint8)
    return a.astype(np.uint8)

def upload_result(out_path):
    """把成品上传为多维表格附件, 返回 file_token"""
    size = os.path.getsize(out_path)
    with open(out_path, "rb") as f:
        req = UploadAllMediaRequest.builder().request_body(
            UploadAllMediaRequestBody.builder()
            .file_name(os.path.basename(out_path))
            .parent_type("bitable_file")
            .parent_node(APP_TOKEN)
            .size(size)
            .file(f)
            .build()
        ).build()
        resp = client.drive.v1.media.upload_all(req)
    if not resp.success():
        raise RuntimeError(f"上传成品失败: code={resp.code} msg={resp.msg}")
    return resp.data.file_token


def update_record(record_id, fields):
    """更新记录字段"""
    req = UpdateAppTableRecordRequest.builder() \
        .app_token(APP_TOKEN).table_id(TABLE_ID).record_id(record_id) \
        .request_body(
            AppTableRecord.builder().fields(fields).build()
        ).build()
    resp = client.bitable.v1.app_table_record.update(req)
    if not resp.success():
        raise RuntimeError(f"更新记录失败: code={resp.code} msg={resp.msg}")


def notify_reviewers(record_id):
    """给每个审核人发飞书消息"""
    base_url = os.environ.get("FEISHU_BASE_URL", "https://example.feishu.cn").rstrip("/") + f"/base/{APP_TOKEN}?table={TABLE_ID}"
    text = f"有一张新图已处理完成, 待审核。\n请到多维表格查看并审核。\n{base_url}"
    for open_id in REVIEWER_OPEN_IDS:
        req = CreateMessageRequest.builder().receive_id_type("open_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            ).build()
        resp = client.im.v1.message.create(req)
        if not resp.success():
            log.warning("通知 %s 失败: %s", open_id, resp.msg)


def resolve_choice(fields, field_name, mapping, default):
    """读取单选列的中文选择, 翻译成实际模型名; 没选或选项不认识就用默认"""
    val = fields.get(field_name)
    # 单选字段可能是字符串, 也可能是 {"text": "..."} 之类, 统一取文字
    if isinstance(val, dict):
        val = val.get("text") or val.get("name")
    if isinstance(val, list) and val:
        v0 = val[0]
        val = v0.get("text") or v0.get("name") if isinstance(v0, dict) else v0
    return mapping.get(val, default)


def process_one(record):
    rid = record.record_id
    log.info("===== 开始处理记录 %s =====", rid)
    fields = record.fields or {}
    # 读取员工在表格里选的模型(没选就用默认)
    sr_model = resolve_choice(fields, F_SR_MODEL, SR_MODEL_MAP, SR_MODEL_DEFAULT)
    cut_model = resolve_choice(fields, F_CUT_MODEL, CUT_MODEL_MAP, CUT_MODEL_DEFAULT)
    target_long = resolve_choice(fields, F_QUALITY, QUALITY_MAP, QUALITY_DEFAULT)
    # 处理模式: "保守留全" => conservative(尽量多保留, 交人工); 其他/默认 => 标准
    mode_val = fields.get(F_MODE)
    if isinstance(mode_val, dict):
        mode_val = mode_val.get("text") or mode_val.get("name")
    if isinstance(mode_val, list) and mode_val:
        v0 = mode_val[0]
        mode_val = v0.get("text") or v0.get("name") if isinstance(v0, dict) else v0
    conservative = (mode_val == MODE_CONSERVATIVE)
    log.info("使用模型 -> 超分: %s | 抠图: %s | 画质长边: %s | 模式: %s",
             sr_model, cut_model, target_long, "保守留全" if conservative else "标准")
    # 先标记处理中, 避免下一轮重复处理
    update_record(rid, {F_STATUS: ST_DOING})
    src = download_src(record)
    sr = run_superres(src, rid, sr_model, target_long)
    out = run_cutout(sr, rid, cut_model, conservative=conservative)
    token = upload_result(out)
    update_record(rid, {F_OUT: [{"file_token": token}], F_STATUS: ST_REVIEW})
    notify_reviewers(rid)
    log.info("===== 记录 %s 处理完成, 已置待审核 =====", rid)


def main():
    log.info("服务启动, 每 %s 秒轮询一次 ...", POLL_INTERVAL)
    while True:
        try:
            records = list_pending_records()
            if records:
                log.info("发现 %d 条待处理记录", len(records))
            for rec in records:
                try:
                    process_one(rec)
                except Exception as e:
                    log.exception("处理记录 %s 出错: %s", rec.record_id, e)
                    try:
                        update_record(rec.record_id,
                                      {F_STATUS: ST_FAILED, F_ERR: str(e)[:500]})
                    except Exception:
                        pass
        except Exception as e:
            log.exception("轮询出错: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

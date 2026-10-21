# -*- coding: utf-8 -*-
"""
从魔搭(ModelScope)下载 FLUX GGUF 量化模型全套, 带断点续传和大小校验。

为什么要换: flux1-schnell-fp8.safetensors 是 16.05GiB, 比这台机器的物理内存
(15.8GiB)还大, 连着跑几张必然报 read_file_slice failed。换成量化版后
体积降到 7.7GB, 内存压力一下就没了。

用法:
    python download_models.py            # 下载缺的文件(已下好的自动跳过)
    python download_models.py --check    # 只检查, 不下载

断了直接重新跑, 会从断点接着下, 不用从头来。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# ================== 【配置区】 ==================

from paths import MODELS_DIR   # 换机器只需改 paths.py

HOST = "https://www.modelscope.cn"

# (魔搭仓库, 仓库内文件名, 放到 models 下的哪个子目录, 说明)
FILES = [
    ("city96/FLUX.1-schnell-gguf", "flux1-schnell-Q5_K_S.gguf", "unet",
     "主模型(量化版, 替代 17GB 的 fp8)"),
    ("city96/t5-v1_1-xxl-encoder-gguf", "t5-v1_1-xxl-encoder-Q5_K_M.gguf", "text_encoders",
     "T5 文本编码器(量化版)"),
    ("comfyanonymous/flux_text_encoders", "clip_l.safetensors", "text_encoders",
     "CLIP-L 文本编码器"),
    ("black-forest-labs/FLUX.1-schnell", "ae.safetensors", "vae",
     "VAE 解码器"),
    # 抠图模型: 目录名必须是 RMBG-2.0, 且这几个配置文件缺一不可(只下 model.safetensors 会报错)
    ("1038lab/RMBG-2.0", "model.safetensors", "background_removal/RMBG-2.0", "抠图模型"),
    ("1038lab/RMBG-2.0", "config.json", "background_removal/RMBG-2.0", "抠图模型配置"),
    ("1038lab/RMBG-2.0", "configuration.json", "background_removal/RMBG-2.0", "抠图模型配置"),
    ("1038lab/RMBG-2.0", "birefnet.py", "background_removal/RMBG-2.0", "抠图模型代码"),
    ("1038lab/RMBG-2.0", "BiRefNet_config.py", "background_removal/RMBG-2.0", "抠图模型代码"),
]

CHUNK = 1024 * 1024          # 每次读 1MB
RETRY = 5                    # 断了重试几次

# ================== 【配置区】结束 ==================

# 魔搭是国内站, 走科学上网代理反而绕到国外, 实测 0.3MB/s。
# 这里强制直连, 不管系统/环境变量里配了什么代理, 用户不用手动去关 FlClash。
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def urlopen(req, timeout):
    return _direct.open(req, timeout=timeout)


def log(msg):
    print(msg, flush=True)


def human(size):
    return "%.2f GB" % (size / 2 ** 30) if size >= 2 ** 30 else "%.1f MB" % (size / 2 ** 20)


def remote_size(repo, name):
    """问魔搭这个文件多大, 用来校验下完整没有"""
    url = "%s/api/v1/models/%s/repo/files?Revision=master" % (HOST, repo)
    with urlopen(urllib.request.Request(url), timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for f in data.get("Data", {}).get("Files") or []:
        if f["Name"] == name:
            return f["Size"]
    raise RuntimeError("仓库 %s 里没有 %s" % (repo, name))


def download(repo, name, dest, expected):
    """带断点续传地下载。已经下完的直接返回。"""
    done = os.path.getsize(dest) if os.path.exists(dest) else 0
    if done == expected:
        log("  已完整, 跳过")
        return True
    if done > expected:
        log("  本地文件比远端还大, 可能是坏的, 重下")
        os.remove(dest)
        done = 0

    url = "%s/models/%s/resolve/master/%s" % (HOST, repo, name)
    for attempt in range(1, RETRY + 1):
        try:
            req = urllib.request.Request(url)
            if done:
                req.add_header("Range", "bytes=%d-" % done)
                log("  从 %s 处续传 ..." % human(done))
            with urlopen(req, timeout=60) as resp, open(dest, "ab") as f:
                started = time.time()
                last_print = 0
                while True:
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    now = time.time()
                    if now - last_print >= 5:
                        speed = done / max(now - started, 0.1) / 2 ** 20
                        log("  %s / %s  (%.0f%%)  %.1f MB/s" % (
                            human(done), human(expected), done * 100.0 / expected, speed))
                        last_print = now
            if done == expected:
                log("  下载完成 %s" % human(done))
                return True
            log("  大小对不上(%s / %s), 继续续传" % (human(done), human(expected)))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log("  第%d次中断: %s" % (attempt, e))
            time.sleep(5)
        done = os.path.getsize(dest) if os.path.exists(dest) else 0

    log("  × 重试 %d 次仍未下完。再跑一次本脚本会从断点继续。" % RETRY)
    return False


def main():
    parser = argparse.ArgumentParser(description="下载 FLUX GGUF 全套模型")
    parser.add_argument("--check", action="store_true", help="只检查状态, 不下载")
    args = parser.parse_args()

    log("目标目录: %s" % MODELS_DIR)
    log("")

    plan = []
    total_todo = 0
    for repo, name, subdir, note in FILES:
        folder = os.path.join(MODELS_DIR, subdir)
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        try:
            size = remote_size(repo, name)
        except Exception as e:
            log("× %s 查不到大小: %s" % (name, e))
            continue
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        state = "完整" if have == size else ("缺 %s" % human(size - have) if have else "未下载")
        log("%-42s %8s  %s  [%s]" % (name, human(size), state, note))
        log("    -> models\\%s\\" % subdir)
        if have != size:
            total_todo += size - have
            plan.append((repo, name, dest, size))

    log("")
    if not plan:
        log("所有模型文件都齐了。")
        return 0
    log("待下载合计 %s" % human(total_todo))
    if args.check:
        return 0

    log("")
    failed = []
    for repo, name, dest, size in plan:
        log("下载 %s" % name)
        if not download(repo, name, dest, size):
            failed.append(name)
        log("")

    if failed:
        log("以下文件没下完, 再跑一次本脚本会续传: %s" % ", ".join(failed))
        return 1
    log("全部下载完成。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

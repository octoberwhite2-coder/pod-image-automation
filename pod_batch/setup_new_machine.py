# -*- coding: utf-8 -*-
"""
新机器一键配置: 装 GGUF 插件 + 下模型 + 铺工作流和T恤底图。

前提(这两步得你先手动做好):
  1. 新机器上已经装好 ComfyUI(整合包解压即可), 且能正常启动一次
  2. 本目录(pod_batch)和上一级的 env_config.py / .env 已经拷过来

然后运行:
    python setup_new_machine.py

跑完重启 ComfyUI, 就可以双击"一键批量出图.bat"了。
"""

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

import paths
import download_models

GGUF_PLUGIN_URL = "https://codeload.github.com/city96/ComfyUI-GGUF/zip/refs/heads/main"
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")


def log(msg):
    print(msg, flush=True)


def step(n, title):
    log("")
    log("=" * 56)
    log("  第%d步  %s" % (n, title))
    log("=" * 56)


def install_gguf_plugin():
    target = os.path.join(paths.CUSTOM_NODES_DIR, "ComfyUI-GGUF")
    if os.path.isfile(os.path.join(target, "nodes.py")):
        log("  已装过, 跳过")
        return True

    log("  从 GitHub 下载插件 ...")
    try:
        # 插件在 GitHub, 这个走系统代理反而快, 所以不像下模型那样强制直连
        data = urllib.request.urlopen(GGUF_PLUGIN_URL, timeout=180).read()
    except Exception as e:
        log("  × 下载失败: %s" % e)
        log("    GitHub 连不上的话, 手动下载 https://github.com/city96/ComfyUI-GGUF")
        log("    解压后把里面那层文件夹改名成 ComfyUI-GGUF, 放到:")
        log("    %s" % paths.CUSTOM_NODES_DIR)
        return False

    archive = zipfile.ZipFile(io.BytesIO(data))
    root = archive.namelist()[0].split("/")[0]
    tmp = os.path.join(paths.CUSTOM_NODES_DIR, "_gguf_tmp")
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    archive.extractall(tmp)
    # 压缩包里套了一层 ComfyUI-GGUF-main, 要脱掉这层再放进去
    shutil.move(os.path.join(tmp, root), target)
    shutil.rmtree(tmp, ignore_errors=True)
    log("  已安装到 %s" % target)
    return True


def install_gguf_dependency():
    if not os.path.exists(paths.EMBEDED_PYTHON):
        log("  × 找不到内嵌 python: %s" % paths.EMBEDED_PYTHON)
        log("    如果不是整合包(比如自己 venv 装的 ComfyUI), 请在那个环境里手动:")
        log("    pip install gguf")
        return False

    check = subprocess.run(
        [paths.EMBEDED_PYTHON, "-c", "import gguf; print(gguf.__version__)"],
        capture_output=True, text=True)
    if check.returncode == 0:
        log("  gguf 已装 (%s)" % check.stdout.strip())
        return True

    log("  往 ComfyUI 内嵌 python 里装 gguf ...")
    result = subprocess.run(
        [paths.EMBEDED_PYTHON, "-m", "pip", "install", "--no-warn-script-location", "gguf>=0.13.0"],
        capture_output=True, text=True)
    if result.returncode != 0:
        log("  × 安装失败:\n%s" % result.stdout[-800:])
        return False
    log("  安装完成")
    return True


def copy_assets():
    ok = True
    for name, target_dir in (("white.png", paths.INPUT_DIR),
                             ("black.png", paths.INPUT_DIR),
                             ("POD.json", paths.WORKFLOWS_DIR)):
        src = os.path.join(ASSETS, name)
        if not os.path.exists(src):
            log("  × assets 里缺 %s" % name)
            ok = False
            continue
        os.makedirs(target_dir, exist_ok=True)
        dst = os.path.join(target_dir, name)
        if os.path.exists(dst):
            log("  %s 已存在, 跳过(不覆盖你机器上的版本)" % name)
            continue
        shutil.copy2(src, dst)
        log("  %s -> %s" % (name, target_dir))
    return ok


def check_credentials():
    try:
        sys.path.insert(0, os.path.dirname(HERE))
        import env_config  # noqa: F401
    except SystemExit as e:
        log("  × 凭证没配好: %s" % e)
        return False
    except ImportError:
        log("  × 找不到上一级目录的 env_config.py, 把它和 .env 一起拷过来")
        return False
    log("  飞书凭证读取正常")
    return True


def main():
    log("ComfyUI 根目录: %s" % paths.COMFY_ROOT)

    results = {}

    step(1, "检查飞书凭证")
    results["凭证"] = check_credentials()

    step(2, "安装 ComfyUI-GGUF 插件")
    results["插件"] = install_gguf_plugin()

    step(3, "安装 gguf 依赖")
    results["依赖"] = install_gguf_dependency()

    step(4, "下载模型(约 12GB, 断了重跑本脚本会续传)")
    results["模型"] = download_models.main() == 0

    step(5, "铺工作流和T恤底图")
    results["素材"] = copy_assets()

    log("")
    log("=" * 56)
    for name, ok in results.items():
        log("  %-6s %s" % (name, "OK" if ok else "没搞定, 看上面的提示"))
    log("=" * 56)

    if all(results.values()):
        log("")
        log("全部就绪。接下来:")
        log("  1. 重启 ComfyUI(装了新插件必须重启才认)")
        log("  2. 双击 一键批量出图.bat")
        return 0
    log("")
    log("有步骤没完成, 修好后再跑一次本脚本(已完成的会自动跳过)。")
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

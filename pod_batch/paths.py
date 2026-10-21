# -*- coding: utf-8 -*-
"""
ComfyUI 的位置。换机器部署时, 只有这一个文件可能需要改。

优先级:
  1. 环境变量 COMFY_ROOT
  2. 同目录下的 comfy_root.txt (第一行写路径)
  3. 下面 CANDIDATES 里能找到的第一个
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env_config  # noqa: E402  COMFY_ROOT may be supplied in the local .env.

# 常见位置, 按顺序试。新机器装在别处就设环境变量或写 comfy_root.txt, 不用改代码。
CANDIDATES = [
    r"E:\ComfyUI\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\ComfyUI",
    r"D:\ComfyUI\ComfyUI_windows_portable\ComfyUI",
    r"C:\ComfyUI\ComfyUI_windows_portable\ComfyUI",
]

_HERE = os.path.dirname(os.path.abspath(__file__))
_HINT_FILE = os.path.join(_HERE, "comfy_root.txt")


def _looks_like_comfy(path):
    """靠 main.py + models 目录判断这确实是 ComfyUI 根目录"""
    return (path and os.path.isfile(os.path.join(path, "main.py"))
            and os.path.isdir(os.path.join(path, "models")))


def find_comfy_root():
    env = os.environ.get("COMFY_ROOT")
    if env and _looks_like_comfy(env):
        return env

    if os.path.exists(_HINT_FILE):
        with open(_HINT_FILE, "r", encoding="utf-8") as f:
            hinted = f.readline().strip().strip('"')
        if _looks_like_comfy(hinted):
            return hinted

    for candidate in CANDIDATES:
        if _looks_like_comfy(candidate):
            return candidate

    raise SystemExit(
        "找不到 ComfyUI 目录。\n"
        "请在 %s 里写上 ComfyUI 的完整路径(就是含 main.py 的那一层),\n"
        "例如:  D:\\ComfyUI\\ComfyUI_windows_portable\\ComfyUI" % _HINT_FILE
    )


COMFY_ROOT = find_comfy_root()
MODELS_DIR = os.path.join(COMFY_ROOT, "models")
INPUT_DIR = os.path.join(COMFY_ROOT, "input")
CUSTOM_NODES_DIR = os.path.join(COMFY_ROOT, "custom_nodes")
WORKFLOWS_DIR = os.path.join(COMFY_ROOT, "user", "default", "workflows")
# 整合包的内嵌 python, 装插件依赖要用它(不是系统 python)
EMBEDED_PYTHON = os.path.join(os.path.dirname(COMFY_ROOT), "python_embeded", "python.exe")


if __name__ == "__main__":
    print("ComfyUI 根目录 : %s" % COMFY_ROOT)
    print("模型目录       : %s" % MODELS_DIR)
    print("输入目录       : %s" % INPUT_DIR)
    print("自定义节点     : %s" % CUSTOM_NODES_DIR)
    print("工作流目录     : %s" % WORKFLOWS_DIR)
    print("内嵌 python    : %s  %s" % (
        EMBEDED_PYTHON, "(在)" if os.path.exists(EMBEDED_PYTHON) else "(找不到)"))

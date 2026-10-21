# -*- coding: utf-8 -*-
"""
把现有工作流从"单文件 Checkpoint"改成"GGUF 量化三件套"。

原来: Checkpoint加载器(flux1-schnell-fp8, 16GB) 一个节点同时给出 MODEL/CLIP/VAE
现在: Unet Loader (GGUF)      -> MODEL
      DualCLIPLoader (GGUF)   -> CLIP   (T5 量化版 + CLIP-L)
      VAE加载器                -> VAE

只改模型加载这一段, 后面生图/去背景/套图的连线原样不动。
生成 workflow_api_gguf.json, 原文件不覆盖。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "workflow_api.json")
DST = os.path.join(HERE, "workflow_api_gguf.json")

UNET_GGUF = "flux1-schnell-Q5_K_S.gguf"
T5_GGUF = "t5-v1_1-xxl-encoder-Q5_K_M.gguf"
CLIP_L = "clip_l.safetensors"
VAE_NAME = "ae.safetensors"

# 新节点编号, 挑几个不会和现有节点撞的
N_UNET, N_CLIP, N_VAE = "200", "201", "202"


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        graph = json.load(f)

    checkpoints = [nid for nid, n in graph.items()
                   if n.get("class_type") == "CheckpointLoaderSimple"]
    if len(checkpoints) != 1:
        raise SystemExit("预期正好 1 个 Checkpoint加载器, 实际找到 %d 个" % len(checkpoints))
    old = checkpoints[0]
    print("原 Checkpoint 节点: %s (%s)" % (old, graph[old]["inputs"].get("ckpt_name")))

    for nid in (N_UNET, N_CLIP, N_VAE):
        if nid in graph:
            raise SystemExit("节点编号 %s 已被占用, 换一个" % nid)

    graph[N_UNET] = {
        "inputs": {"unet_name": UNET_GGUF},
        "class_type": "UnetLoaderGGUF",
        "_meta": {"title": "Unet Loader (GGUF)"},
    }
    graph[N_CLIP] = {
        # clip_name1=T5, clip_name2=CLIP-L, type=flux
        "inputs": {"clip_name1": T5_GGUF, "clip_name2": CLIP_L, "type": "flux"},
        "class_type": "DualCLIPLoaderGGUF",
        "_meta": {"title": "DualCLIPLoader (GGUF)"},
    }
    graph[N_VAE] = {
        "inputs": {"vae_name": VAE_NAME},
        "class_type": "VAELoader",
        "_meta": {"title": "VAE加载器"},
    }

    # Checkpoint加载器的三个输出槽: 0=MODEL 1=CLIP 2=VAE, 分别改接到新节点
    slot_to_node = {0: N_UNET, 1: N_CLIP, 2: N_VAE}
    rewired = 0
    for nid, node in graph.items():
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) == old:
                slot = value[1]
                if slot not in slot_to_node:
                    raise SystemExit("节点 %s 的 %s 用了没预料到的输出槽 %s" % (nid, key, slot))
                node["inputs"][key] = [slot_to_node[slot], 0]
                print("  改接 节点%s.%s : %s槽%s -> %s" % (
                    nid, key, old, slot, slot_to_node[slot]))
                rewired += 1

    del graph[old]
    print("已删除原 Checkpoint 节点, 共改接 %d 处连线" % rewired)

    with open(DST, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    print("已写出: %s" % DST)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

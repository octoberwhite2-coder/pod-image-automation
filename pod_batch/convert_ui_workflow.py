# -*- coding: utf-8 -*-
"""
把 ComfyUI 界面上保存的工作流(POD.json)从"单文件 Checkpoint"改成"GGUF 三件套"。

和 make_gguf_workflow.py 的区别: 那个改的是脚本用的 API 格式,
这个改的是界面用的 UI 格式(带坐标和连线表), 让你在 ComfyUI 里看到的也是新模型。

做法: 保留原有连线的 id 不变, 只把连线的"来源节点"从 Checkpoint 改到三个新节点,
      这样下游所有节点(采样器/文本编码/VAE解码)一律不用动。

原文件会先备份成 POD.json.fp8bak
"""

import json
import os
import shutil
import sys

UI_WORKFLOW = (r"E:\ComfyUI\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable"
               r"\ComfyUI\user\default\workflows\POD.json")

UNET_GGUF = "flux1-schnell-Q5_K_S.gguf"
T5_GGUF = "t5-v1_1-xxl-encoder-Q5_K_M.gguf"
CLIP_L = "clip_l.safetensors"
VAE_NAME = "ae.safetensors"

N_UNET, N_CLIP, N_VAE = 200, 201, 202


def make_node(node_id, node_type, title, pos, widgets, inputs, output_name, output_type, links):
    return {
        "id": node_id,
        "type": node_type,
        "pos": pos,
        "size": [340, 30 + 26 * len(widgets)],
        "flags": {},
        "order": 0,
        "mode": 0,
        "inputs": [
            {"name": name, "type": "COMBO", "widget": {"name": name}, "link": None}
            for name in inputs
        ],
        "outputs": [
            {"name": output_name, "type": output_type, "slot_index": 0, "links": links}
        ],
        "properties": {"Node name for S&R": node_type},
        "widgets_values": widgets,
        "title": title,
    }


def main():
    if not os.path.exists(UI_WORKFLOW):
        raise SystemExit("找不到界面工作流: %s" % UI_WORKFLOW)

    backup = UI_WORKFLOW + ".fp8bak"
    if not os.path.exists(backup):
        shutil.copy2(UI_WORKFLOW, backup)
        print("已备份原工作流 -> %s" % os.path.basename(backup))

    with open(UI_WORKFLOW, "r", encoding="utf-8") as f:
        wf = json.load(f)

    ckpt = [n for n in wf["nodes"] if n["type"] == "CheckpointLoaderSimple"]
    if len(ckpt) != 1:
        raise SystemExit("预期正好 1 个 Checkpoint 节点, 实际 %d 个" % len(ckpt))
    ckpt = ckpt[0]
    old_id = ckpt["id"]
    pos = ckpt.get("pos", [48, 192])
    print("原 Checkpoint 节点 id=%s (%s)" % (old_id, ckpt["widgets_values"][0]))

    # 按输出槽收集它原来带的连线: 0=MODEL 1=CLIP 2=VAE
    slot_links = {0: [], 1: [], 2: []}
    for link in wf["links"]:
        if link[1] == old_id:
            slot_links[link[2]].append(link[0])
    print("  MODEL 连线 %s | CLIP 连线 %s | VAE 连线 %s"
          % (slot_links[0], slot_links[1], slot_links[2]))

    # 改连线来源: 指向新节点的 0 号槽
    new_origin = {0: N_UNET, 1: N_CLIP, 2: N_VAE}
    for link in wf["links"]:
        if link[1] == old_id:
            link[1] = new_origin[link[2]]
            link[2] = 0

    # 删掉老节点, 加上三个新节点
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] != old_id]
    wf["nodes"].extend([
        make_node(N_UNET, "UnetLoaderGGUF", "Unet Loader (GGUF)",
                  [pos[0], pos[1]], [UNET_GGUF], ["unet_name"],
                  "MODEL", "MODEL", slot_links[0]),
        make_node(N_CLIP, "DualCLIPLoaderGGUF", "DualCLIPLoader (GGUF)",
                  [pos[0], pos[1] + 120], [T5_GGUF, CLIP_L, "flux"],
                  ["clip_name1", "clip_name2", "type"],
                  "CLIP", "CLIP", slot_links[1]),
        make_node(N_VAE, "VAELoader", "VAE加载器",
                  [pos[0], pos[1] + 270], [VAE_NAME], ["vae_name"],
                  "VAE", "VAE", slot_links[2]),
    ])

    wf["last_node_id"] = max(wf.get("last_node_id", 0), N_VAE)

    with open(UI_WORKFLOW, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=2)

    print("已写入界面工作流, 节点数 %d, 连线数 %d" % (len(wf["nodes"]), len(wf["links"])))
    print("在 ComfyUI 里重新打开 POD 工作流即可看到新的三个加载器节点。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

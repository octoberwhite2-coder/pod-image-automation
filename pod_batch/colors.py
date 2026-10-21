# -*- coding: utf-8 -*-
"""
T恤颜色定义 —— 套图底图、店铺色库别名、印花配色建议, 都在这里对齐。

为什么需要别名: 同一个颜色在不同店铺的色库里叫法不同(有的写"黑色", 有的写"Black"),
而 ERP 的 pubPrepare 是按名字精确匹配色库的, 对不上就抛
"品类[棉T]无色库模板(颜色名需与色库一致)"。所以套图用统一的 key,
建上架任务时再按目标店铺翻译成它自己的叫法。

加一个新颜色要做两件事:
  1. 把底图放进 ComfyUI/input/  (和 white.png 同规格: 1024x1024 平铺正面图)
  2. 在下面 COLORS 里加一行

没有底图的颜色会被自动跳过, 不会上到 SKC 里。
"""

import os

import paths

# key: 内部统一标识(文件名也用它)
#   template  底图文件名, 放在 ComfyUI/input/ 下
#   aliases   各店铺色库里可能出现的写法, 匹配时全部试一遍(不分大小写)
#   dark      深色衣服。浅色图案印在深色衣服上才看得清, 反之亦然, 用来做配色提示
COLORS = {
    "white":  dict(template="white.png",  aliases=["白色", "White"],            dark=False, label="白色"),
    "black":  dict(template="black.png",  aliases=["黑色", "Black"],            dark=True,  label="黑色"),

    # ---- 下面这些色库里有, 但还没有底图, 补了图去掉注释即可 ----
    # "grey":        dict(template="grey.png",        aliases=["Grey", "灰色"],        dark=False, label="灰色"),
    # "dark_grey":   dict(template="dark_grey.png",   aliases=["Dark Grey"],           dark=True,  label="深灰"),
    # "light_grey":  dict(template="light_grey.png",  aliases=["Light Grey"],          dark=False, label="浅灰"),
    # "navy":        dict(template="navy.png",        aliases=["Blue", "蓝色"],        dark=True,  label="蓝色"),
    # "red":         dict(template="red.png",         aliases=["Red", "红色"],         dark=True,  label="红色"),
    # "burgundy":    dict(template="burgundy.png",    aliases=["Burgundy"],            dark=True,  label="酒红"),
    # "green":       dict(template="green.png",       aliases=["Green", "绿色"],       dark=True,  label="绿色"),
    # "army_green":  dict(template="army_green.png",  aliases=["Army Green"],          dark=True,  label="军绿"),
    # "mint_green":  dict(template="mint_green.png",  aliases=["Mint Green"],          dark=False, label="薄荷绿"),
    # "yellow":      dict(template="yellow.png",      aliases=["Yellow", "黄色"],      dark=False, label="黄色"),
    # "pink":        dict(template="pink.png",        aliases=["Pink", "粉色"],        dark=False, label="粉色"),
    # "hot_pink":    dict(template="hot_pink.png",    aliases=["Hot Pink", "玫红色"],  dark=True,  label="玫红"),
    # "coral_pink":  dict(template="coral_pink.png",  aliases=["Coral Pink"],          dark=False, label="珊瑚粉"),
    # "purple":      dict(template="purple.png",      aliases=["Purple", "紫色"],      dark=True,  label="紫色"),
    # "violet":      dict(template="violet.png",      aliases=["Violet Purple"],       dark=True,  label="紫罗兰"),
    # "apricot":     dict(template="apricot.png",     aliases=["Apricot", "杏色"],     dark=False, label="杏色"),
    # "beige":       dict(template="beige.png",       aliases=["Beige"],               dark=False, label="米色"),
    # "brown":       dict(template="brown.png",       aliases=["Brown", "棕色"],       dark=True,  label="棕色"),
    # "khaki":       dict(template="khaki.png",       aliases=["Khaki"],               dark=False, label="卡其"),
    # "bronze":      dict(template="bronze.png",      aliases=["Bronze"],              dark=True,  label="古铜"),
}

# 色库里有但不做套图的: Multicolor / Black and White 是花色, 没有单一底图可套
SKIP = ["Multicolor", "Black and White"]


def available():
    """底图实际存在的颜色。没有底图的不参与出图和上架。"""
    out = {}
    for key, conf in COLORS.items():
        if os.path.exists(os.path.join(paths.INPUT_DIR, conf["template"])):
            out[key] = conf
    return out


def missing():
    """配置了但底图不在的"""
    return [k for k, c in COLORS.items()
            if not os.path.exists(os.path.join(paths.INPUT_DIR, c["template"]))]


def alias_map():
    """
    {店铺色库里的写法(小写): 内部key} —— 建上架任务时用它把套图对到店铺色库。
    """
    m = {}
    for key, conf in COLORS.items():
        for a in conf["aliases"]:
            m[a.lower().strip()] = key
    return m


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    have = available()
    print("底图目录: %s" % paths.INPUT_DIR)
    print()
    print("可用颜色 %d 个:" % len(have))
    for k, c in have.items():
        print("  %-12s %-16s 别名: %s" % (k, c["template"], " / ".join(c["aliases"])))
    miss = missing()
    if miss:
        print()
        print("配置了但缺底图 %d 个(会被跳过): %s" % (len(miss), ", ".join(miss)))
    print()
    print("色库写法 -> 内部key 的映射:")
    for a, k in sorted(alias_map().items()):
        print("  %-16s -> %s" % (a, k))

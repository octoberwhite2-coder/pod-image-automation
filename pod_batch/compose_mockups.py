# -*- coding: utf-8 -*-
"""
把透明图案精准套到T恤底图上, 并产出印刷用的成品文件。

为什么不在 ComfyUI 里做:
  原来的做法是把整张 1024x1024 画布缩到固定尺寸贴死坐标。可图案在画布里的
  位置和大小完全看 FLUX 当时画在哪 —— 所以永远对不准。这一步是确定性的
  图像运算, 放在 Python 里做才能精确控制。

做法:
  1. 按 alpha 通道裁掉透明边, 只留图案本身
  2. 对每个印刷位, 等比缩放装进去(contain, 不裁切不变形)
  3. 按锚点定位
  4. 每个印刷位另存一份 300dpi 印刷件

默认导出正面大图。左胸印刷位保留为注释示例，启用前需根据自己的底图重新校准。

用法:
    python compose_mockups.py --calibrate      # 在底图上画出所有印刷位, 校准用
    python compose_mockups.py                  # 处理 output/png 下所有透明图
    python compose_mockups.py --only 15 21     # 只处理指定编号
"""

import argparse
import os
import re
import sys

from PIL import Image, ImageDraw, ImageChops

import model_templates

# ================== 【配置区】 ==================

PNG_DIR = None
OUT_DIR = None
PRINT_DIR = None


def default_paths():
    """Resolve the legacy ComfyUI location only when that mode is requested."""
    import paths
    return tuple(os.path.join(paths.COMFY_ROOT, "output", name) for name in ("png", "mockup", "print"))

# T恤底图由 colors.py 统一管理(底图文件名 + 各店铺色库的别名)。
# 加颜色只需: 把底图放进 ComfyUI/input/ + 在 colors.py 里去掉对应那行注释。
# 没有底图的颜色会自动跳过, 不会出套图, 也不会进上架任务。

# 印刷位。box 是底图上的像素坐标 (左, 上, 右, 下), inches 是实际印刷尺寸。
# 标尺: 底图 1024x1024, 衣服躯干宽约 415px 对应实物约 20 英寸, 即 ~21px/英寸。
#
# 注意"左胸"指穿着者的左胸 —— 正面照里它在画面的右半边。
#
# 位置不准就跑 --calibrate, 会把所有框画在底图上, 照着改这里的数字。
PLACEMENTS = {
    "front": {
        "box": (383, 375, 633, 708),   # 12x16 英寸, 领口下方约 3 英寸起
        "inches": (12, 16),
        "anchor": "top",
        "label": "正面大图",
    },
    # 左胸位默认关闭，启用后需用自己的底图校准。
    # 背面需要另备一张背面视角的底图。
    # "chest": {
    #     "box": (549, 378, 623, 452),   # 3.5x3.5 英寸, 穿着者左胸(画面右半边)
    #     "inches": (3.5, 3.5),
    #     "anchor": "center",
    #     "label": "左胸小图",
    # },
}

PRINT_DPI = 300

# ================== 【配置区】结束 ==================


def log(msg):
    print(msg, flush=True)


def trim_alpha(image, threshold=8):
    """
    按 alpha 裁掉四周的透明边, 只留图案本身。
    这是精准定位的前提 —— 不裁的话贴上去的是整张画布, 位置全看运气。
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    alpha = image.getchannel("A")
    # 低于阈值的当成全透明, 避免边缘那圈几乎看不见的半透明像素撑大边界
    bbox = alpha.point(lambda v: 255 if v > threshold else 0).getbbox()
    if not bbox:
        return None
    return image.crop(bbox)


def fit_into(image, box_width, box_height):
    """等比缩放到刚好装进指定尺寸, 不裁切不变形"""
    scale = min(box_width / image.width, box_height / image.height)
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )


def place(art, template_image, placement):
    """把裁好的图案按某个印刷位放到底图上"""
    model_templates.validate_placement(placement, template_image.size)
    left, top, right, bottom = placement["box"]
    fitted = fit_into(art, right - left, bottom - top)

    x = left + (right - left - fitted.width) // 2      # 水平永远居中于印刷位
    if placement.get("anchor", "center") == "center":
        y = top + (bottom - top - fitted.height) // 2
    else:
        y = top                                        # 顶部对齐

    canvas = template_image.convert("RGBA").copy()
    overlay = Image.new("RGBA", canvas.size)
    overlay.alpha_composite(fitted, (x, y))
    if placement.get("occlusion_polygons"):
        mask = Image.new("L", canvas.size, 255)
        draw = ImageDraw.Draw(mask)
        for polygon in placement["occlusion_polygons"]:
            draw.polygon([tuple(point) for point in polygon], fill=0)
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
    canvas.alpha_composite(overlay)
    return canvas.convert("RGB"), fitted


def make_print_file(art, placement):
    """按印刷位的实际尺寸生成 300dpi 印刷件, 背景透明"""
    width = int(placement["inches"][0] * PRINT_DPI)
    height = int(placement["inches"][1] * PRINT_DPI)
    fitted = fit_into(art, width, height)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    top = 0 if placement["anchor"] == "top" else (height - fitted.height) // 2
    canvas.alpha_composite(fitted, ((width - fitted.width) // 2, top))
    return canvas


def load_templates(registry_path=None, template_ids=None):
    """读入所有有底图的颜色, 返回 {颜色key: 底图}"""
    if registry_path is not None:
        return model_templates.load(registry_path, template_ids)
    if template_ids is not None:
        raise ValueError("--templates requires --registry")
    import colors
    import paths
    loaded = {}
    for key, conf in colors.available().items():
        loaded[key] = Image.open(os.path.join(paths.INPUT_DIR, conf["template"]))
    missing = colors.missing()
    if missing:
        log("  (%d 个颜色缺底图, 已跳过: %s)" % (len(missing), ", ".join(missing)))
    return loaded


def placements_for(image):
    """Registered photos have their own placement; legacy callers keep defaults."""
    entry = image.info.get("pod_template")
    return {"front": entry["placement"]} if entry else PLACEMENTS


def calibrate(templates=None):
    """Draw photo boundaries and occlusions without inferring physical dimensions."""
    global OUT_DIR
    if OUT_DIR is None:
        OUT_DIR = default_paths()[1]
    os.makedirs(OUT_DIR, exist_ok=True)
    templates = load_templates() if templates is None else templates
    for name, image in templates.items():
        canvas = image.convert("RGB").copy()
        draw = ImageDraw.Draw(canvas)
        for key, placement in placements_for(image).items():
            model_templates.validate_placement(placement, image.size)
            left, top, right, bottom = placement["box"]
            draw.rectangle([left, top, right - 1, bottom - 1], outline=(255, 0, 0), width=3)
            for polygon in placement.get("occlusion_polygons", []):
                draw.polygon([tuple(p) for p in polygon], outline=(0, 140, 255), width=2)
            draw.text((left + 4, top + 4), key, fill=(255, 0, 0))
            log("  %s %s: %dx%d px; inches=%s" %
                (name, key, right - left, bottom - top, placement.get("inches", "not specified")))
        out = os.path.join(OUT_DIR, "_calibrate_%s.png" % name)
        canvas.save(out)
        log("  -> %s" % out)
    log("Red: print boundary; blue: occlusion. Recalibrate when the photo changes.")


def item_id_of(filename):
    """文件名形如 21_single_continuous_line_..._00001_.png, 取开头的编号"""
    return filename.split("_", 1)[0]


def compose_one(png_path, templates=None, save_print=True):
    """
    处理一张透明图: 裁边 -> 套到每个底图的每个印刷位 -> 存印刷件。
    返回 {"底图_印刷位": 套图路径}, 例如 {"white_front": "...", "white_chest": "..."}。
    图案整张透明时返回 {}。
    批量入口和出图流程都走这个函数, 保证两边行为一致。
    """
    global OUT_DIR, PRINT_DIR
    templates = load_templates() if templates is None else templates
    if not templates:
        return {}
    with Image.open(png_path) as art:
        trimmed = trim_alpha(art)
    if trimmed is None:
        return {}

    if OUT_DIR is None:
        OUT_DIR = default_paths()[1]
    os.makedirs(OUT_DIR, exist_ok=True)
    stem = re.sub(r"_\d{5,}_$", "", os.path.splitext(os.path.basename(png_path))[0])

    results = {}
    for template_name, template_image in templates.items():
        for key, placement in placements_for(template_image).items():
            mockup, _ = place(trimmed, template_image, placement)
            out = os.path.join(OUT_DIR, "%s_%s_%s.png" % (stem, template_name, key))
            mockup.save(out)
            results["%s_%s" % (template_name, key)] = out

    if save_print:
        print_jobs = {}
        for template_name, template_image in templates.items():
            for key, placement in placements_for(template_image).items():
                if "inches" not in placement:
                    log("  %s: no physical inches; keeping source art, skipping print canvas" % template_name)
                    continue
                # Preserve legacy names; registered photos get independent print files.
                name = "%s_%s" % (template_name, key) if "pod_template" in template_image.info else key
                print_jobs[name] = placement
        if print_jobs:
            if PRINT_DIR is None:
                PRINT_DIR = default_paths()[2]
            os.makedirs(PRINT_DIR, exist_ok=True)
        for name, placement in print_jobs.items():
            make_print_file(trimmed, placement).save(
                os.path.join(PRINT_DIR, "%s_%s_print.png" % (stem, name)),
                dpi=(PRINT_DPI, PRINT_DPI))
    return results


def main():
    parser = argparse.ArgumentParser(description="精准套图 + 生成印刷文件")
    parser.add_argument("--calibrate", action="store_true", help="只画印刷位方框用来校准")
    parser.add_argument("--only", nargs="*", help="只处理这些编号")
    parser.add_argument("--no-print-file", action="store_true", help="不生成 300dpi 印刷文件")
    parser.add_argument("--registry", help="User-owned template registry JSON")
    parser.add_argument("--templates", nargs="+", help="Template IDs from --registry")
    parser.add_argument("--png-dir", help="Transparent input directory")
    parser.add_argument("--out-dir", help="Mockup output directory")
    parser.add_argument("--print-dir", help="Physical print output directory")
    args = parser.parse_args()
    if args.templates and not args.registry:
        parser.error("--templates requires --registry")
    global PNG_DIR, OUT_DIR, PRINT_DIR
    defaults = tuple(os.path.join("output", name) for name in ("png", "mockup", "print")) if args.registry else default_paths()
    PNG_DIR = args.png_dir or defaults[0]
    OUT_DIR = args.out_dir or defaults[1]
    PRINT_DIR = args.print_dir or defaults[2]
    templates = load_templates(args.registry, args.templates)
    if not templates:
        raise SystemExit("No enabled templates available")

    if args.calibrate:
        calibrate(templates)
        return 0

    if not os.path.isdir(PNG_DIR):
        raise SystemExit("找不到透明图目录: %s" % PNG_DIR)

    files = sorted(f for f in os.listdir(PNG_DIR) if f.lower().endswith(".png"))
    if args.only:
        wanted = set(args.only)
        files = [f for f in files if item_id_of(f) in wanted]
    if not files:
        raise SystemExit("没有要处理的透明图")

    done = 0
    for filename in files:
        path = os.path.join(PNG_DIR, filename)
        trimmed = trim_alpha(Image.open(path))
        if trimmed is None:
            log("  ! %s 整张都是透明的, 跳过" % filename)
            continue
        ratio = trimmed.width / trimmed.height
        shape = "横" if ratio > 1.15 else ("竖" if ratio < 0.87 else "方")
        log("%s  (裁后 %dx%d, 宽高比 %.2f %s)"
            % (filename.rsplit("_0", 1)[0], trimmed.width, trimmed.height, ratio, shape))
        compose_one(path, templates, save_print=not args.no_print_file)
        done += 1

    log("")
    count = sum(len(placements_for(image)) for image in templates.values())
    log("Processed %d patterns, %d mockups per pattern." % (done, count))
    log("  Mockups -> %s" % OUT_DIR)
    if not args.no_print_file:
        log("  Physical print files (only when inches are configured) -> %s" % PRINT_DIR)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

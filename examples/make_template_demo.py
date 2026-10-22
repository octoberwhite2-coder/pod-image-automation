"""Create synthetic canvases and artwork for an offline template demo."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="output/template-demo")
    args = parser.parse_args()
    root = Path(args.output)
    if root.exists():
        parser.error("Output already exists; choose a new directory")
    (root / "png").mkdir(parents=True)
    entries = []
    for ident, size, box, color in [
        ("demo_shirt", (320, 400), [70, 100, 250, 320], (230, 230, 230)),
        ("demo_hoodie", (360, 480), [85, 140, 275, 390], (45, 50, 65)),
    ]:
        image = Image.new("RGB", size, color)
        ImageDraw.Draw(image).text((12, 12), "SYNTHETIC TEMPLATE", fill=(130, 150, 170))
        image.save(root / (ident + ".png"))
        placement = {"box": box, "anchor": "center"}
        if ident == "demo_hoodie":
            placement["occlusion_polygons"] = [[[85, 140], [110, 140], [100, 205]]]
        entries.append({"id": ident, "garment": "tshirt" if ident == "demo_shirt" else "hoodie",
                        "color": "light" if ident == "demo_shirt" else "dark",
                        "file": ident + ".png", "size": list(size), "placement": placement})
    art = Image.new("RGBA", (160, 200))
    draw = ImageDraw.Draw(art)
    draw.ellipse((20, 20, 140, 140), fill=(225, 80, 50, 255))
    draw.rectangle((40, 130, 120, 180), fill=(35, 150, 130, 255))
    art.save(root / "png" / "1_demo.png")
    (root / "templates.json").write_text(
        json.dumps({"schema_version": 1, "templates": entries}, indent=2), encoding="utf-8")
    print("Synthetic demo ready: " + str(root))


if __name__ == "__main__":
    main()

# 多模特模板

模板模式用唯一编号区分不同款式和底图。同色模特也可以在同一次命令中分别输出，不按颜色覆盖。普通 CSV、飞书及 ERP 入口仍使用原有默认底图；本次没有将注册表接入远端 ERP 服务端。

## 无账号离线演示

安装基础依赖后，在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
python examples/make_template_demo.py --output output/template-demo
python pod_batch/compose_mockups.py --registry output/template-demo/templates.json --png-dir output/template-demo/png --out-dir output/template-demo/mockups --print-dir output/template-demo/print
python pod_batch/compose_mockups.py --registry output/template-demo/templates.json --out-dir output/template-demo/calibration --calibrate
```

不需要 ComfyUI、GPU、飞书或 ERP 账号。演示生成两张纯色合成底图、一张几何图案及注册表；不是商品照片或生产素材。生成器拒绝覆盖已有目录。默认演示不配置实物尺寸，因此只出套图，不出物理印刷画布。

## 自备模板

将注册表和自己有权使用的底图保存在同一私有目录。例如：

```json
{
  "schema_version": 1,
  "templates": [
    {
      "id": "my_shirt_white",
      "garment": "tshirt",
      "color": "white",
      "enabled": true,
      "file": "images/white.png",
      "size": [1200, 1600],
      "placement": {
        "box": [300, 400, 900, 1100],
        "anchor": "center"
      }
    }
  ]
}
```

以上坐标仅为格式示例，必须按自己的底图校准。用 `--templates my_shirt_white another_id` 选择编号；不传则处理所有启用模板。编号允许 ASCII 字母、数字、下划线和连字符，最多 64 字符，禁止 Windows 保留设备名；大小写不同的重复编号也会被拒绝。

- `file` 使用相对注册表目录的路径和正斜杠。拒绝绝对路径、越出注册表目录的路径及指向目录外的符号链接。
- `size` 必须与图片实际宽高一致；替换照片后尺寸不一致会停止处理，要求重新校准。
- `box` 为整数像素坐标，顺序是左、上、右、下，右下不含。图案等比缩放，默认居中，也可用 `anchor: "top"` 顶部对齐。
- `placement.occlusion_polygons` 可填多个多边形，每个至少三个整数点，用于保留照片中头发等前景遮挡。此操作只影响套图，原透明图及独立印刷画布不受遮挡掩模影响。
- `placement.inches` 可选，仅填写已确认的实际印刷宽高。没有该项时跳过 300 DPI 印刷画布。照片像素尺寸不能推导实物英寸。

`--calibrate` 输出红色印花边界和蓝色遮挡线。照片或姿势变化后，即使像素尺寸相同也应重新人工校准。

## 输出与兼容

注册模板输出名包含模板 ID；物理印刷件也包含 ID，避免同色不同款式互相覆盖。传统无注册表模式保留原有颜色键、默认印刷位及输出方式。文件名仅移除末尾形如 _00001_ 的 ComfyUI 计数后缀，普通 _01 / _02 编号会保留；同一 ComfyUI 图案的不同计数版本仍共用输出名，应选择需要的输入版本。注册表模式目录默认是当前目录下的 `output/png`、`output/mockup`、`output/print`，可用三个目录参数覆盖。

`compose_one` 接受 `load_templates(registry_path=..., template_ids=...)` 返回的图片字典。脚本集成时明确设置输出目录；空字典不会回退加载默认业务底图。

此功能验证的是套图几何关系，不会修补已经缺边的原图，也不证明图案原创性或侵权风险。检查原图、透明图和套图各阶段的完整性。

## 私有配置

建议把自备注册表放在仓库外，或放入已忽略的 `local_templates/`。不要上传底图、来源商品图、真实业务文案或生产目录。示例只提供生成合成素材的代码。

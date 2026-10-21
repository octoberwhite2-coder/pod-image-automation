# POD Image Automation

用于按需印刷（Print on Demand）的图片自动化脚本：ComfyUI 批量生图、透明图套版、300 DPI 印刷文件，以及可选的飞书 / ERP 接入。

## 当前版本：现有源码基线

首次提交保留本地已有脚本结构和主要行为，只做公开发布必需的脱敏。后续可移植性、配置和测试改进以独立 Git 提交保留。

| 入口 | 用途 |
| --- | --- |
| `pod_batch/comfy_batch.py` | CSV 提示词 → ComfyUI 队列 → 结果查询 |
| `pod_batch/compose_mockups.py` | 按透明边界裁切，等比贴到正面 / 左胸印刷位，导出印刷件 |
| `cutout_service.py` | 飞书附件 → Real-ESRGAN 超分 → rembg 抠图 → 回写 |
| `pod_batch/feishu_batch.py` | 飞书提示词表批量出图和回写 |
| `pod_batch/erp_batch.py` | 自建 ERP 图案接口批量出图和回写 |
| `pod_batch/setup_new_machine.py` | 原有 Windows ComfyUI 安装辅助脚本 |

这是已有生产脚本的公开基线，并非已验证的跨平台一键安装包。部分辅助脚本的表 ID 已替换为 `EXAMPLE_FEISHU_ID`，使用前须按自己的表重新配置。旧配置模块在导入时要求飞书配置，后续版本将解除无关入口的配置耦合。

## 运行条件

- Python；本项目的模型和依赖兼容性推荐使用 Python 3.11 或 3.12 单独环境。
- ComfyUI 由用户自行安装，默认 API 为 `http://127.0.0.1:8188`。
- CSV 客户端只用标准库；套图需要 Pillow；抠图服务还需要 numpy、opencv-python-headless、lark-oapi、rembg 及其推理后端。
- 工作流涉及 FLUX / GGUF 和 RMBG 节点：模型权重、ComfyUI 自定义节点及其许可须分别准备。仓库不附模型。
- 套图底图（`white.png` / `black.png`）由用户提供，放在自己的 ComfyUI `input` 目录；仓库不包含原业务商品图片。
- ERP 入口对接特定自建接口，不是通用 ERP 客户端，服务端不在本仓库。

将 `.env.example` 复制为 `.env` 并填写自己使用的服务。不要将本地 `.env` 或业务 CSV 提交 Git。

```powershell
python pod_batch/comfy_batch.py --help
python pod_batch/comfy_batch.py --csv YOUR_PROMPTS.csv --dry-run
```

`--dry-run` 对 CSV 入口不提交任务；飞书 / ERP 入口仍会读取远端数据。迁移、建表、回写及安装脚本会改变对应系统，仅在了解用途后手动执行。

## 发布范围与敏感信息

本仓库排除了 `.env`、旧备份、日志、历史提示词 / 批次数据、迁移快照、客户及店铺诊断脚本、ERP 服务端、原商品图片、二进制依赖和模型。硬编码凭证改为读取本地配置；私有地址和表 ID 改为示例。诊断脚本不再输出访问令牌及记录内容。

本仓库从经过检查的文件清单建立新的 Git 历史，不复制原工作目录或其他仓库的 Git 对象。公开前执行已知本地敏感值比对、Gitleaks 扫描和语法检查；这些检查不能替代后续新增文件的人工复核。

## 许可与第三方组件

本仓库自有脚本与文档使用 MIT 许可。ComfyUI、rembg、Real-ESRGAN、自定义节点、模型权重和用户图片分别遵循其原有许可；MIT 不授予这些外部素材的使用权。工作流中的上游参考链接保留，下载辅助脚本不会自动授予模型使用许可。

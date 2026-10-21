# POD Image Automation

面向按需印刷（Print on Demand）的图片自动化脚本：用 ComfyUI 批量生成图案，将透明图等比套到服装底图，并导出 300 DPI 印刷文件。支持 CSV 提示词，以及可选的飞书 / 自建 ERP 接入。

现有本地工具经脱敏后发布，首个源码提交保留原有结构；配置与测试改进放在后续独立提交中。更新记录见 [CHANGELOG](CHANGELOG.md)。

## 能做什么

| 入口 | 用途 |
| --- | --- |
| `pod_batch/comfy_batch.py` | CSV → ComfyUI 队列，支持任务筛选、seed、演练和等待结果 |
| `pod_batch/compose_mockups.py` | 按透明边界裁切、等比套图、导出 300 DPI 透明印刷件 |
| `cutout_service.py` | 飞书附件 → Real-ESRGAN 超分 → rembg 抠图 → 回写与通知 |
| `pod_batch/feishu_batch.py` | 读取飞书提示词表，生图、套图并回写 |
| `pod_batch/erp_batch.py` | 读取自建 ERP 图案队列，生图、套图并回写 |

默认启用正面印刷位（12 × 16 英寸，3600 × 4800 px）。左胸位保留为注释示例，需自行启用和校准。仓库不包含业务提示词、服装底图、账户配置或模型权重。

## 先做一次离线演练

在仓库根目录运行以下命令。CSV 演练只使用 Python 标准库，不需要 GPU、ComfyUI、飞书或 ERP 账户：

```powershell
python pod_batch/comfy_batch.py --csv examples/prompts.example.csv --dry-run
```

示例包含两条合成提示词，预期结尾显示“会提交 2 条, 跳过 0 条”。演练只检查 CSV 的筛选和提示词构造，不验证模型、节点或 ComfyUI 服务是否可用。

CSV 支持 `编号`、`主体`、`风格`、`配色`、`完整提示词`、`状态`、`seed`。优先使用完整提示词，否则按主体 / 风格 / 配色构造。默认处理状态为空、`待生成` 或 `待生产` 的行；`--all` 忽略状态。复制示例后编辑自己的本地 CSV，避免把业务数据提交 Git。

## 连接 ComfyUI 生图

自行安装 ComfyUI、工作流所需的自定义节点和模型。默认 API 地址为 `http://127.0.0.1:8188`。根据机器上的实际文件，在 `pod_batch/workflow_api.json` 中核对模型名和节点类型；也可通过 `--workflow` 指定其他兼容的 API 格式工作流。

需要改配置时，将 `.env.example` 复制为 `.env`，填写用到的选项：

```dotenv
COMFY_HOST=http://127.0.0.1:8188
COMFY_ROOT=D:/ComfyUI/ComfyUI
```

`COMFY_ROOT` 指含 `main.py` 和 `models/` 的目录。CSV 提交本身仅需 API 地址，套图等后续步骤才需要这个目录。确认配置后执行：

```powershell
python pod_batch/comfy_batch.py --csv YOUR_PROMPTS.csv --wait
```

该命令会实际提交生图任务。默认保存节点 `9` 输出到 ComfyUI 的 `output/raw/`，节点 `44` 输出到 `output/png/`；套图和印刷件需要再执行下一步。更换工作流时同步调整 `SAVE_PREFIX`，并保留正向提示词节点与采样器结构。详细约定见 [工作流说明](docs/workflows.md)。

## 套图与印刷文件

为脚本准备独立 Python 环境，然后安装基础依赖：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

将自己有权使用的 `white.png` / `black.png` 底图放入 ComfyUI 的 `input/`。默认坐标以 1024 × 1024 底图为基准，换底图后必须重新校准印刷位：

```powershell
.venv\Scripts\python pod_batch/compose_mockups.py --calibrate
.venv\Scripts\python pod_batch/compose_mockups.py
```

校准后在 `compose_mockups.py` 的 `PLACEMENTS` 中调整坐标、英寸尺寸及对齐方式。输出位于 ComfyUI 的 `output/mockup/` 和 `output/print/`。套图使用等比缩放，印刷件保留透明背景；未提供底图的颜色会跳过。

## 可选集成

飞书、ERP 和抠图服务的配置说明见 [configuration.md](docs/configuration.md)。只使用 CSV 或套图时无需填写这些账户。抠图服务另需 `requirements-cutout.txt` 中的可选依赖，以及 Real-ESRGAN 程序和相应模型；建议使用单独的 Python 3.11 / 3.12 环境准备这些依赖。

ERP 是特定自建接口的客户端，服务端不在仓库中。飞书 / ERP 的 `--dry-run` 仍会读取远端数据。旧迁移、建表、上传、安装和模型下载脚本保留供熟悉流程的使用者参考，运行前阅读脚本；这些操作可能写入外部系统或下载大文件。

## 测试与验证范围

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

离线测试使用合成 CSV、临时图片与模拟请求，覆盖配置优先级、凭证检查、工作流复制、CSV 筛选、透明裁切、印刷尺寸、ERP 错误隐藏和禁止认证重定向。验证不调用生产接口，不执行模型推理。

本次发布在 Windows / Python 3.14 上通过离线测试；完整 GPU 生图、飞书、ERP、第三方节点及可选抠图依赖尚未做公开版端到端验证。旧 Windows 安装辅助脚本不属于跨平台一键安装器。

## 敏感信息与许可

公开版本排除了 `.env`、备份、日志、历史提示词 / 批次数据、迁移快照、客户与店铺诊断脚本、ERP 服务端、原商品图片、二进制依赖和模型。凭证从本地配置读取，私有地址与表 ID 已移除。发布前进行了已知敏感值比对及 Gitleaks 扫描，后续提交也需要复核。详见 [SECURITY.md](SECURITY.md)。

本仓库自有脚本与文档使用 [MIT 许可](LICENSE)。ComfyUI、rembg、Real-ESRGAN、自定义节点、模型权重和用户图片分别遵循其原有许可；仓库的 MIT 许可不授予这些外部素材的使用权。

# DeepSeek V4.1 Flash 提示词探索实验

使用固定 DSH SDK Minimal＋官方 `str_replace_editor`，在 `effect-sse-httpapi-streaming` 上比较 B（baseline）、U（用户祷告）、S（系统祷告）、F（前沿身份）四组。每组固定两次，共 8 次，串行执行，不追加第三次或自动补跑。

**当前预算：总计 ¥200，API 硬上限 ¥160，单次正式运行 ¥20，环境与余量 ¥40。时间保持不变：agent 3 小时、verifier 30 分钟、整轮 24 小时、串行执行。** 若资源不足或费用未知，程序会停下并保留证据；不会自动扩大实验。

[公开实时看板](https://deepseek-prompt-lab.javavirtualenv.chatgpt.site) · [公开仓库](https://github.com/Haedus-Industries/oh-my-god-deepseek)

## 本地准备

Python 3.12/3.13、uv、Git；正式环境必须为 Linux x86_64＋Docker Compose。首次安装从已提交的 `uv.lock` 读取版本和发布包哈希。Codex Cloud 使用来源可追踪的官方镜像单层派生版本，支持现有 vfs，无需修改平台配置。

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked --group dev --extra analysis
uv run --locked bench prepare
uv run --locked bench dry-run --output outputs/dry-run
uv run --locked pytest -q --junitxml=outputs/pytest-results.xml
uv run --locked bench analyze-selection
uv run --locked bench doctor
```

`prepare` 获取公开资源并校验，不读取 API key，不调用模型。Windows 的原生 Minimal 使用持久 PowerShell，因此本地模拟标记为 `cloud_ready=false`；Cloud 上还需验证 Linux bash。`doctor` 在没有 Docker/资源不足时退出 2，不能将本地模拟成功视为容器验证成功。

## Cloud 操作

详见 [Cloud 操作说明](docs/CLOUD.md)、[当前验收状态](docs/PREPARATION_STATUS.md) 和 [开始测试 Prompt](docs/START_TEST_PROMPT.md)。先把本项目推送到用于 Codex Cloud 的 Git 仓库，再配置环境。不要上传 `.cache`、`.venv`、`outputs` 或任何密钥。

```bash
bash scripts/cloud-setup.sh
# 在运行阶段提供 DEEPSEEK_API_KEY；不要把它写入文件。
uv run --locked bench doctor --runtime --containers
# 上述准备和 doctor 不产生付费模型调用。
# 付费启动命令见 docs/CLOUD.md，仅在用户明确要求开始后执行。
```

`run` 是单一前台协调器，管理串行调度、停止信号、账本和最终摘要。再次对相同目录执行会恢复任务，并读取已完成结果；不会重跑已完成的付费运行。`report` 只读已有资料，不调用模型。流中断或 usage 缺失保留费用预留，禁止自动重发该请求；这种运行也不自动替换。

## 资源与实验约束

- SDK/runtime `0.1.5rc1`；Pier `0c802fc067a425345b24d1c69411aa98acf61a1d`；DeepSWE `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea`。详见 `resources/pins.json`、`resources/resolved.json`、`uv.lock`。
- 官方任务说明原文保存在 `resources/task/instruction.md`，四组逐字共用。祷文在 `resources/prayer.txt`，没有界面的 `agent-teams` 标记。系统身份只在指定层级变动；真实路由始终 `deepseek-flash`。
- 固定 SDK 实际使用 **OpenAI 兼容 Chat Completions 流式协议**，上游为 `https://mono.guimc.ltd/v1/chat/completions`；请求含 `thinking.type=enabled`、`reasoning_effort=max`、`max_tokens=256000`，上下文 1M。其他采样参数沿用原生默认，原始请求保存实际值。
- 每次新建容器、工作树、DSH home 和 session。四组两轮按种子 `20261004` 随机排序。联网搜索、团队与辅助模型不在工具 schema 中。
- 模型不会接收人工澄清；其澄清请求作为结果记录。能力只看官方 verifier，模型自评不计分。
- 官方 collector 只读 HEAD。评测侧在模型停止后记录原始 Git 状态、打包工作树，再创建标明评测身份的快照提交，使未提交和新文件也进入官方补丁。验收测试保持原文。

## 证据与报告

`outputs/experiment` 包含：

|路径|内容|
|---|---|
|`state.json`、`summary.json`|调度、运行状态、替换次数、结束原因、总账摘要|
|`ledger.sqlite`|持久共享预留和 usage 结算；未知费用保留|
|`gateway/<attempt>/api/<id>/`|原始非凭据请求、SSE 响应、推理文本、usage、价格或未知费用标记|
|`pier/<attempt>/agent/`|实际提示、SDK 通知、DSH session、工具轨迹、worker 结束状态|
|`pier/<attempt>/artifacts/`|完整源码工作树压缩包、最终补丁、模型结束时 Git 状态|
|`pier/<attempt>/verifier/`|官方判分、测试日志、CTRF 报告|
|`price/`|执行时官方价格页、时间、哈希与解析费率|
|`report.md`、`research-summary.json`、`results.csv`|中文报告、机器摘要、固定八次比较|
|`report/`|逐次提取的推理、回答、工具调用，供盲审|

本地 `dry-run` 的模拟费用只是账本测试数据，真实费用为零。推理文本只指 API 返回的内容，不声称是完整内部思维链。关键词/命令分类用于定位资料，需人工盲审；单题小样本不支持总体能力或绝对上界的结论。

## 官方来源

- [DSH Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/python-sdk.md)
- [DeepSeek 官方评测说明](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/evaluation/README.md)
- [官方价格与路由](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSWE v1.1 累计公开数据](https://deepswe.datacurve.ai/artifacts/v1.1/trials.json)
- [Codex Cloud 环境说明](https://learn.chatgpt.com/docs/environments/cloud-environment)

选题数据与计算说明见 [实验协议](docs/PROTOCOL.md)。安装、准备和报告不会自动升级依赖；需要变更资源时应显式更新锁文件并重新验证。

本地验收结果与 Cloud 尚待完成的检查见 [准备阶段记录](docs/PREPARATION_STATUS.md)，累计选题数据说明见 [选题依据](docs/SELECTION.md)。

## 公开实时看板

`dashboard/` 提供 Sites 公开只读页面、D1 状态和 R2 全文存储。评测侧每 10 秒增量上报，网页每 5 秒读取；Cloud 离线后仍可查看已收到资料。四组各两次结果、预算截断与基础设施错误分开，推理、回答和工具全文可下载。

Cloud 运行阶段配置独立上传令牌，并将其哈希登记为 Sites secret；原始令牌和 API key 不进入网页或模型环境。命令支持 `run --dashboard-url`、`doctor --dashboard-url`、`sync-dashboard` 和 `dashboard-token-config`，均见 [看板操作说明](docs/DASHBOARD.md) 与 [数据协议](docs/DASHBOARD_PROTOCOL.md)。

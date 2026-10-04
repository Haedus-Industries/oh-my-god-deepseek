# Codex Cloud 执行说明

## 条件

Linux x86_64；Docker daemon 和 `docker compose` 可用；至少 4 vCPU、16 GiB 可用内存、20 GiB 可用磁盘。磁盘值只是允许进入无付费容器预检的门槛，不保证所有 Docker 存储驱动都能解包大型固定镜像；运行期间仍应保留并监控剩余空间。`doctor` 同时考虑 Linux cgroup CPU/内存限制。最多两个并发任务环境各限制 2 CPU／8 GiB；这些是容器上限而非内存预留，agent 与 verifier 在每个 trial 内阶段化运行，不额外要求四个环境同时常驻。容器预检会并发运行 base 与 oracle 两个真实 SWE 控制任务，验证 16 GiB 主机能承载正式调度的双槽资源形状。如果 Cloud 环境无法通过该并发控制、Docker 或磁盘检查，不要进入付费实验，应更换执行环境。

Cloud setup 阶段运行 `bash scripts/cloud-setup.sh`。该阶段只安装 `uv.lock` 中的依赖、拉取固定仓库和镜像、记录镜像 digest、安装与容器 Python ABI 匹配的 worker 依赖并校验全部文件哈希。

首次拉取后 `resources/resolved.json` 中的 digest 是以后运行的唯一镜像引用。生成的任务副本只替换镜像引用和补丁收集 hook；官方任务说明、test.patch、grader.py、test.sh 与 oracle 补丁不变。新 digest 或任何资源漂移都需重新准备和检查。

## 凭据与网络

运行阶段环境变量 `DEEPSEEK_API_KEY` 必须可用。仅在 setup 阶段可见的旧式 Secrets 不能用于此方案；不要通过写入仓库、setup 产物或持久文件绕过生命周期。未配置运行阶段凭据时，`doctor --runtime` 会停下；不会测试付费连通性。

评测侧通过 `https://mono.guimc.ltd/v1/chat/completions` 使用 SDK 原生的 Chat Completions 流式协议，并访问官方价格文档；准备阶段需要 GitHub、PyPI、公开 DeepSWE 数据和官方 ECR 镜像。不要把接入点改成 Responses 协议，也不要回退到 `api.deepseek.com`。进入托管云端后，先读取环境网络策略与 cloud-environment-runtime 技能，使用该环境的代理、CA 和 Docker 配置。本项目的上游 HTTP 客户端继承代理设置与 `SSL_CERT_FILE`／`REQUESTS_CA_BUNDLE`，不关闭 TLS 校验。

真实 API key 只供评测侧 gateway 读取，绝不写入任务配置或传入模型容器。任务容器 `network_mode=none`，仅有本次实验令牌；通过只挂载单个 Unix socket 文件与 gateway 通信。容器内 loopback relay 将 SDK 原生 HTTP 转发到该 socket，因此模型仍能在 localhost 上运行 SSE 项目测试，无法直接联网。gateway 只接受已注册令牌、固定真实模型、固定推理档位、固定提示层级和两项工具；每个请求均先做预算预留。

模型只看到 `/app` 基础工作树、自己的日志、worker 文件、锁定 SDK 依赖和 socket。没有宿主仓库、API key、其他运行目录、隐藏 tests 或参考 solution 的挂载。verifier 在独立环境运行；oracle 对照仅由评测侧执行。

## 付费前检查

```bash
uv run --locked bench doctor --runtime --containers
```

该命令不调用付费 API。它运行：未修改 base 的官方判分（必须不通过）、官方 oracle 判分（必须完整通过）、基础提交及原始工具检查、隐藏资源缺失检查、无外网检查，以及 Linux 原生 SDK 的四组模拟 API 任务。随后在真实任务容器中再运行四组小型模拟 fixture，穿过 Pier、无网络容器、Unix socket、loopback relay 和评测侧 gateway，验证 editor 与持久 bash；该 fixture 不作为能力评测。检查输出保存于 `outputs/preflight`，就绪记录带代码/资源指纹。任何检查失败先诊断环境、判分器或适配器；不改验收测试迎合结果。

Windows 的模拟结果不能替代此步骤。容器 socket 路径与配额由正式 adapter 使用；还应保留 Docker 原始错误以便诊断。`run` 会重新检查资源、运行阶段 key、价格和模型别名，要求本次指纹的完整就绪记录。

## 前台运行与恢复

```bash
uv run --locked bench run --smoke-only --output outputs/experiment
uv run --locked bench run --output outputs/experiment
```

短回复检查只是协议、参数、日志和计费检查，使用 max 推理和 64 输出 token；不要求特定答案、不校准能力。它计入 API ¥160 账本，成功后正式启动不再次调用；中断或失败后不会自动重发。

协调器默认两次并发，两轮均包含四组；第二轮等待第一轮结束。全部前两次结束后，才判断是否追加第三次。固定前两次依然是主结果。agent 上限 3 小时，verifier 30 分钟；整轮上限从 `state.json` 的 `execution_started_at` 起 24 小时，恢复不重置。单独短回复检查不会提前启动正式实验的 24 小时计时。接近整轮截止时会预留环境准备、快照和判分时间，缩短 agent 可用时间或停止发起新运行。

正常判分与预算/时间截断分开。基础设施错误不当作能力失败，最多补跑两个全新运行；费用仍计入总账。断流、usage 未知或实际请求校验失败会保留证据并停止该组的自动替换，以免未知计费下重发。其它组可继续，直到预算不足或时间届满。

同一目录再次执行 `run` 只恢复未完成状态。完成的尝试有持久记录；已写出的 Pier 结果会在恢复时读取，不再调用模型。如果模型已结束但判分尚未完成，在剩余时间允许时只对保存的补丁补做官方 verifier，不再调用模型。恢复会按本次运行的 Docker project 标签和挂载路径精确清理遗留容器，绝不全局 prune。取消时先保存工作树，再让 Pier 清理容器。中断时已经发出的请求费用不能假定为零；账本保留预留额。不要删除账本、完成记录或改代码/协议后继续同一个实验。内核文件锁防止两个协调器同时收费。

```bash
uv run --locked bench report --output outputs/experiment
```

这一步不产生模型调用。Cloud 结束前保存整个 `outputs/experiment` 目录及资源锁。API key 不包含在交付资料中。

## 费用规则

预算总额 ¥200，其中 API ¥160、每次正式运行 ¥20，环境与余量 ¥40。汇率固定 ¥7.3/$。请求发出前按实时官方价格中的最大费率预留上下文输入＋本次最大输出的最坏费用；SDK 上下文仍为 1,000,000，预留按更保守的 1,048,576 输入 token 覆盖 1M 的单位差异。即使预期命中缓存，也不能用预期费用代替预留。账本使用 SQLite 事务和整数微元，两个并发请求共享额度。

收到完整 usage 和结束标记后，按非缓存输入、缓存输入和输出分别结算，释放差额。API 的 `prompt_tokens` 已含缓存 token，不能再次加上缓存。缓存创建不当作缓存命中。断流、缺 usage 或上游拒绝但计费不明时不自动退回预留。

费率按请求时间选峰谷；跨时段请求采用两端较高费率。当前已核验 2026 年国庆 10 月 1—7 日的节假日窗口；其它未覆盖节假日按保守上界记账，最终与服务商账单核对。[官方定价](https://api-docs.deepseek.com/quick_start/pricing/)、[国务院节假日安排](https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202511/t20251104_4258838.html)。环境实际收费仍由用户提供，API 服务不能代替 Cloud 账单。

最坏预留比实际消费严格：即使还有少量预算，也可能因不足以覆盖下一次最大请求而提前停止。社区记录给出的首八次约 ¥20–31、最多十二次约 ¥30–46 只是规划估算；长推理、额外测试、修复循环会提高费用。扩大预算不改变时间与样本数量。

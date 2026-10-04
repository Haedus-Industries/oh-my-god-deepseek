# 当前准备验收

更新时间：2026-10-04（Asia/Shanghai）。Codex Cloud 串行探索协议 `codex-cloud-serial-v1` 的完整无付费预检已通过，当前源码/资源指纹与就绪记录一致。

- Python **36 项通过**，包含实际 Unix socket 流式中继、固定 SDK、持久 shell、editor、预算、未知费用与恢复；JUnit：`outputs/pytest-cloud-serial.xml`。
- 看板源码类型检查、ESLint、正式构建和 **11 项 API/活动测试通过**。线上版本仍待发布，见 DASHBOARD_STATUS.md。
- 现有 vfs daemon 可运行官方镜像的单层派生版本，大小约 3.78 GB。固定来源 manifest 为 `sha256:6a728da85db92e3fcd4826473886dbfe14f965e8edb8e62707fa0f35f14b681c`；两次导出 tar 摘要完全一致。来源摘要和本地镜像 ID 在资源锁中，不假称原官方 RepoDigest。
- 串行官方控制通过：**base reward 0，oracle reward 1**；oracle 的 47 项新增测试和 70 项保留测试全部通过。agent/verifier 独立阶段使用同一镜像；容器实际限额 2 CPU / 8 GiB，network_mode=none。
- Linux 本地四组 SDK 模拟（16 请求）通过；一个真实容器模拟（4 请求）通过，证明持久 bash、官方 editor、Unix gateway、流式响应与工作树保存。
- 基础与 oracle 控制期间最低可用磁盘超过 17 GiB；不需要修改平台驱动、挂载磁盘或扩容。每次容器启动和运行期间仍检查 3 GiB 余量。
- 官方价格解析、运行阶段凭据存在性及看板协议/稳定实验 ID 鉴权通过。
- **付费模型调用 0**，`outputs/experiment` 尚未创建；没有能力结果。固定八次串行，各组两次，不追加或自动付费补跑；预算和时限见 CLOUD.md。

最新验收：`outputs/doctor-cloud-serial-command.json`（退出 0），就绪记录：`resources/readiness.json`，详细原始证据：`outputs/preflight/2026-10-04T06-46-15.826525+00-00/`。已修复共享镜像被清理和 HTTPConnection 中继关闭错误。

准备授权不包含付费调用。[开始测试 Prompt](START_TEST_PROMPT.md) 可用于下一条消息授权启动。若代码或实验输入改变，需要重新验证受影响的就绪条件。

# Codex Cloud 探索性执行

当前协议为 `codex-cloud-serial-v1`：B/U/S/F 各两次，固定八次串行运行，不追加第三次，不自动补跑基础设施错误。API 上限 ¥160、单次 ¥20、总预算 ¥200；agent 3 小时、独立判分 30 分钟、正式执行 24 小时，恢复不重置期限。

## 当前平台与镜像

使用现有平台 Docker daemon 和 Compose，不修改驱动、data-root 或磁盘。支持 `vfs`。固定官方 linux/amd64 镜像由固定 Crane 0.20.3 合并为最终文件系统，流式 `docker import` 为单层；不落盘保存完整 rootfs tar，不关闭 TLS。Crane 发布包以固定 SHA256 校验，运行设置从来源 config 保留。

`resources/resolved.json` 保存原官方 manifest/config/layer 摘要、派生单层镜像 ID 和导出 tar 摘要。派生镜像不是官方 RepoDigest。准备与验证日志在 `outputs/image-preparation`。之后使用精确本地镜像 ID，不重新拉取多层镜像。

每次只保留一个任务容器。agent 结束后收集补丁和工作树并销毁容器，再启动独立 verifier。verifier 使用同一基础镜像，评测侧只向该容器复制原始隐藏测试文件，不另建多层判分镜像。任务说明、基础提交、官方 test.patch、test.sh、grader 和 oracle 不改。

有效 CPU 至少 2、内存至少 8 GiB；任务限额仍为 2 CPU / 8 GiB。启动容器要求实测镜像大小之外还剩至少 3 GiB；`vfs` 会复制整个 rootfs。该检查在每次容器启动前执行。执行中持续关注空间，不全局 prune；`/tmp` 为 tmpfs，不作为额外免费磁盘。

## 无付费准备

```bash
cd /workspace/oh-my-god-deepseek
export UV_CACHE_DIR="$PWD/.cache/uv"
bash scripts/cloud-setup.sh
uv run --locked pytest -q --junitxml=outputs/pytest-cloud-serial.xml
uv run --locked bench doctor --runtime --containers --dashboard-url "$DSBENCH_DASHBOARD_URL"
```

`prepare --images` 和 `doctor` 不调用付费模型。doctor 串行执行 base/oracle 官方控制，期望 reward 0/1；运行本地四组 SDK 模拟和一个真实容器工具链模拟，验证持久 bash、editor、Unix socket 和无外网容器。就绪记录在 `resources/readiness.json`，预检日志在 `outputs/preflight`，必须与当前代码和资源指纹相符。看板鉴权单独纳入命令结果。

运行阶段使用现有 `DEEPSEEK_API_KEY`、`DSBENCH_DASHBOARD_TOKEN` 和 `DSBENCH_DASHBOARD_URL`，不打印或写入凭据。上游固定 `https://mono.guimc.ltd/v1/chat/completions`，SDK 原生 Chat Completions streaming，不使用 Responses 或更换 API 路由。继承托管代理和 CA，保持 TLS 验证。

## 付费启动与恢复

当前准备任务不会运行下列命令。仅在用户明确要求开始付费测试后执行：

```bash
uv run --locked bench run --smoke-only --output outputs/experiment --dashboard-url "$DSBENCH_DASHBOARD_URL"
uv run --locked bench run --output outputs/experiment --dashboard-url "$DSBENCH_DASHBOARD_URL"
uv run --locked bench report --output outputs/experiment
```

短回复 smoke 计入同一账本，不校准能力；正式启动读取成功记录，不重复调用。稳定实验 ID 为 `flash-minimal-20261004-01`，保留 state、ledger、fingerprint 和 dashboard/outbox.sqlite。未知费用、断流或中断 smoke 不自动重发。基础设施错误保留为错误，继续其它未执行槽位，不购买替换尝试。已保存补丁可只补做判分。

不得更改协议后在已有付费目录继续；不得删除账本、结果或 outbox。串行的八次最坏时长可能超过 24 小时，截止后报告不完整样本。实验目录中的推理、工具日志、工作树和原始判分保留；公开副本脱敏，隐藏测试不上传。

## 计费

API 硬上限 ¥160，单次 ¥20，环境与余量 ¥40 不自动转入 API。汇率 ¥7.3/$。发出请求前按实时官方价格预留最大输入 1,048,576 与输出上限 256,000 token 的费用；usage 完整返回才释放差额。缓存输入不重复计算，未知金额保留占用额度，剩余额度不足以覆盖下一次请求时停止。实际价格和 usage 原文保存，最终与服务商账单核对；环境费用由用户提供。

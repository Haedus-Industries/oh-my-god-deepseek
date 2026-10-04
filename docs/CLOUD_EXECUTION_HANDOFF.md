# DeepSeek 实验 Cloud 执行交接

更新时间：2026-10-04（Asia/Shanghai）

## 交接目标

在可配置托管 Docker 启动参数的位置，将平台 daemon 从 `vfs` 改为受 cgroup 管理的 `overlay2`，完成固定镜像准备和全部无付费容器预检。交接阶段不得启动付费 smoke 或正式 B/U/S/F 运行。

稳定实验 ID 为 `flash-minimal-20261004-01`。若后续位置已经存在正式输出目录，必须沿用目录中的持久实验 ID、状态和账本，不得创建新身份、删除账本或重跑已完成尝试。

## 当前仓库状态

- 工作目录：`/workspace/oh-my-god-deepseek`
- 分支：`work`
- 修改前 HEAD：`f7f1d21`（`Configure Codex Cloud benchmark endpoint`）
- 当前修改尚未提交：
  - `docs/CLOUD.md`
  - `src/dsbench/doctor.py`
  - `tests/test_doctor.py`（新增）
  - 本交接文档
- 已将最低主机内存从 20 GiB 修正为 16 GiB。
- 已将无付费 base/oracle 两个真实 SWE 控制任务改为并发运行，以验证正式调度的双槽资源形状。
- 官方任务、基础提交、镜像引用、容器 2 CPU／8 GiB 上限、判分器、四组提示、模型参数和预算规则均未改变。
- 全套测试结果：33 项通过，JUnit 位于 `outputs/pytest-memory-protocol.xml`。
- `outputs/experiment` 不存在；正式逻辑运行 0，物理尝试 0，基础设施替换 0。
- 付费 API 调用 0；API 已结算估算 ¥0，未知 API 金额 ¥0。

继续前先确认修改仍在：

```bash
cd /workspace/oh-my-god-deepseek
git status --short --branch
git diff --check
uv run --locked pytest -q
```

预期测试为 33 项通过。不要丢弃或覆盖上述未提交修改。

## 已确认的原阻塞

原托管环境的 daemon 由 root 启动：

```text
/usr/local/bin/dockerd ... --storage-driver=vfs
```

环境文件系统总量约 31.45 GiB，失败前约 29.8 GiB 可用。固定镜像有 27 个压缩层，压缩层合计约 1.09 GiB；`vfs` 会为后续层复制完整父文件树，导致解包峰值远高于网络下载大小。实际错误为：

```text
failed to register layer: write /root/.bun/bin/bun: no space left on device
```

失败拉取已自动回滚，当前没有可复用的完整镜像。`resources/resolved.json` 仍没有 `image_digest`、`container_python` 或 `worker_files`，所以 `bench doctor` 正确报告：

```text
Run prepare --images in Cloud first
```

本次还验证了以下替代路径，但均不符合正式协议：

- 内核允许用户命名空间内的 overlay 挂载。
- 工作区本身位于外层 overlay，不能再作为 overlay2 的 upperdir；内核返回 `filesystem ... not supported as upperdir`。
- `/tmp` 是可用的独立 tmpfs，但只有约 8.6 GiB。
- 专用 rootless Docker 能以 `overlay2` 启动，但没有 cgroup 委派，明确警告 `Running in rootless-mode without cgroups`；因此不能执行每容器 2 CPU／8 GiB 限额。
- 所有 rootless/overlay 探针进程和临时文件已经停止并清理，平台 daemon 未被修改。

## 平台配置要求

在创建或重启执行环境前配置平台的 Docker-in-Docker 启动参数：

1. 将平台 DIND 存储驱动设置为 `overlay2`。若平台直接使用仓库技能描述的启动器，对应设置通常是：

   ```text
   CAAS_DIND_STORAGE_DRIVER=overlay2
   ```

   必须在平台 daemon 启动前设置；不要在已运行的 `vfs` daemon 上原地切换驱动。

2. Docker `data-root` 必须位于独立、block-backed 的 ext4 或满足 `d_type` 要求的 xfs 文件系统。不能把 overlay2 upperdir 放在本执行容器的外层 overlay 上。

3. Docker data-root 至少保留 40 GiB，建议 60 GiB，以覆盖固定镜像、两个并发任务可写层、verifier 镜像、构建临时层和证据输出。

4. 保持至少 4 vCPU、16 GiB RAM。daemon 必须由平台 root 管理并支持 cgroup v2；rootless-without-cgroups 不合格。

5. 保留平台注入的 HTTP/HTTPS 代理、Docker registry 配置和 CA 信任。不要关闭 TLS，不要用 host networking 绕过网络策略。

6. 保留当前允许的必要出口：`public.ecr.aws`、`mono.guimc.ltd`、DeepSeek 官方价格文档和公开看板域名。

7. 运行阶段环境变量必须继续提供 `DEEPSEEK_API_KEY`、`DSBENCH_DASHBOARD_TOKEN` 和 `DSBENCH_DASHBOARD_URL`；只检查其就绪状态，不打印值。

## 新环境的第一阶段验收

先读取 `AGENTS.md`（若存在）、`README.md`、`docs/CLOUD.md`、`docs/PROTOCOL.md`、`docs/DASHBOARD.md`、`resources/experiment.json`、`resources/dashboard.json` 以及 cloud-environment-runtime 技能。然后执行：

```bash
cd /workspace/oh-my-god-deepseek

env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS \
  -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock info \
  --format '{{json .}}' \
  | jq '{Driver,DockerRootDir,BackingFilesystem,SupportsDType,NCPU,MemTotal,Warnings}'

docker compose version
df -h /workspace /var/lib/docker
findmnt -T /var/lib/docker -o TARGET,SOURCE,FSTYPE,OPTIONS
```

必须满足：

- `Driver` 为 `overlay2`，不能为 `vfs`。
- data-root 的 backing filesystem 为合格的 ext4/xfs，而不是嵌套 overlay upperdir。
- Docker 没有 rootless-without-cgroups 警告。
- 有效 CPU 不少于 4；cgroup 内存不少于 16 GiB。
- Docker data-root 可用空间建议不少于 60 GiB。

任何一项失败都停止，不进行镜像准备或付费测试。

## 镜像准备与无付费预检

平台验收通过后执行：

```bash
cd /workspace/oh-my-god-deepseek
bash scripts/cloud-setup.sh
```

该脚本只同步锁定依赖、拉取固定镜像、记录 digest、准备 worker 依赖并运行基础 doctor；不会调用模型。完成后检查：

```bash
jq '{image_digest,container_python,worker_file_count:(.worker_files|length)}' \
  resources/resolved.json

uv run --locked pytest -q --junitxml=outputs/pytest-cloud-resume.xml

uv run --locked bench doctor \
  --runtime \
  --containers \
  --dashboard-url "$DSBENCH_DASHBOARD_URL"
```

完整 doctor 必须退出 0，并确认：

- 固定资源与镜像 digest 通过。
- Linux x86_64、Docker、CPU、16 GiB RAM 和磁盘通过。
- 运行阶段 API key 存在；官方价格与固定路由通过。
- base 官方判分为 0，oracle 官方判分为 1。
- base/oracle 两个控制任务在同一预检中并发完成。
- Linux 原生 SDK、bash、官方 editor、Unix socket gateway 和容器无外网隔离通过。
- 看板协议版本、写入鉴权和实验 ID `flash-minimal-20261004-01` 通过。
- `paid_api_calls` 仍为 0。

就绪证据应写入 `resources/readiness.json`，并与当前源码/资源 fingerprint 一致。任何预检失败都应保留 `outputs/preflight` 和 `outputs/doctor.json`，不得进入付费阶段。

## 看板状态

- 公共 URL：<https://deepseek-prompt-lab.javavirtualenv.chatgpt.site>
- Sites project ID：`appgprj_6ac1b4fe606c81918b2290f4ce280229`
- 当前已保存站点版本：3
- Site 环境修订：3
- `DSBENCH_UPLOAD_HASHES` 已作为 secret 存在。
- 当前运行阶段令牌对 `flash-minimal-20261004-01` 的鉴权与协议预检已经通过。
- 公开访问方式未改变；不要创建新 Site、重建数据库或覆盖其它仍有效授权。

更换执行环境后必须重新运行带 `--dashboard-url` 的 doctor；不要仅凭旧回执假定新环境的令牌或网络仍然有效。

## 正式运行暂停点

本交接的终点是完整无付费 `doctor --runtime --containers` 通过。当前要求仍是**不要开始正式测试**，因此不要执行：

```bash
uv run --locked bench run --smoke-only --output outputs/experiment
uv run --locked bench run --output outputs/experiment
```

待明确解除暂停后，才按原始预算与恢复规则使用同一实验 ID 和 `outputs/experiment`。不要为了获得更漂亮的结果追加样本，不要把软件模拟或 base/oracle 控制结果计为能力结果。

## 不得变更的实验约束

- 真实模型固定为 `deepseek-flash`，`reasoning_effort=max`。
- 使用仓库锁定的 DSH Minimal、官方 editor、任务、基础提交和判分器。
- 不启用团队、搜索、联网工具或外部辅助模型。
- B/U/S/F 各前两次；全部首次结果完成后，仅正常判分一过一不过的组追加第三次。
- 最多 12 次逻辑运行、2 次基础设施替换、并发最多 2。
- API 硬上限 ¥160、总预算 ¥200、单次正式运行 ¥20。
- agent 3 小时、verifier 30 分钟、整轮 24 小时；恢复不重置期限。
- 不删除账本、完成记录、看板 outbox 或原始资料；不重复已完成或费用未知的请求。

# 公开实时看板

看板使用 ChatGPT Sites 的 Worker、D1 和 R2，任何人无需登录即可读取。源码随本公开仓库保存，实验资料长期保留；首版没有实验控制按钮或额外模型调用。

当前公开地址：[DeepSeek 提示词实验](https://deepseek-prompt-lab.javavirtualenv.chatgpt.site)。发布与软件验证记录见 [DASHBOARD_STATUS.md](DASHBOARD_STATUS.md)。

## Cloud 配置

站点已发布；正式实验的上传授权仍须按以下步骤配置。部署验证令牌将在验证后撤销，不复用为正式凭据。

需要运行阶段可用的 `DSBENCH_DASHBOARD_TOKEN`（建议至少 32 个随机字节）与 `DSBENCH_DASHBOARD_URL`。上传令牌独立于 `DEEPSEEK_API_KEY`，只用于评测侧上报，不能传入模型容器。setup-only secrets 不能绕过生命周期限制。

Cloud 配好随机令牌后，为本次实验选择稳定 ID，例如 `flash-minimal-20261004`，输出令牌的哈希配置：

```bash
uv run --locked bench dashboard-token-config --experiment-id flash-minimal-20261004
```

命令只输出 `DSBENCH_UPLOAD_HASHES` JSON，不输出令牌。将 JSON 在 Sites 的运行环境变量中保存为同名 **secret**，然后发布一个应用该环境版本的站点版本。其格式为实验 ID 到 SHA-256(token) 的映射；添加实验时保留其它仍需上报的实验授权。Cloud 与 Sites 均不需要把原始令牌写入仓库或持久配置。

```bash
uv run --locked bench doctor --dashboard-url "$DSBENCH_DASHBOARD_URL"
uv run --locked bench doctor --runtime --containers --dashboard-url "$DSBENCH_DASHBOARD_URL"
uv run --locked bench run --dashboard-url "$DSBENCH_DASHBOARD_URL" --output outputs/experiment
uv run --locked bench report --output outputs/experiment
uv run --locked bench sync-dashboard --dashboard-url "$DSBENCH_DASHBOARD_URL" --output outputs/experiment
```

第一条包含普通本地资源预检；Windows 没有 Docker 时整体仍会退出 2，即使看板单独检查成功。正式 `run` 的上传身份／协议检查在付费连通性检查之前。观察网络故障不触发模型重发，不修改实验结果。运行阶段使用托管环境代理与 CA，不关闭 TLS 验证。

`scripts/cloud-run.sh` 配置 `DSBENCH_DASHBOARD_URL` 后自动加入上报参数。Cloud 正常结束最多等待 30 秒同步；尚未发送的资料由 `sync-dashboard` 补传。保留整个实验输出目录，其 `dashboard/outbox.sqlite` 包含稳定实验 ID、发送序号、游标及待发送的脱敏资料；不可只复制原始日志而丢弃发送队列后继续同一实验。

## 页面解释

模型工作默认按来源时间连续呈现推理、回答和工具调用，输出折叠在对应调用下；分类只过滤已读取资料。接收游标用于增量去重，不作为活动时间顺序。暂停跟随或向上滚动时保留阅读位置；到达历史页边界会明确暂停，使用“下一页”或“回到最新”继续。已有历史页起点不被刷新改写。


主要结果固定为 B/U/S/F 各前两次；第三次单列。逻辑运行如 B1 与物理尝试如 B1-a1、B1-a2 分开。预算截断、基础设施错误、未判分不能计成普通失败。所有能力结果来自官方 verifier，模型自报完成不算通过。

费用分别展示已结算估算、正在预留、费用未知；后两者占用额度但没有被当成已付账单。usage 尚未完整返回的请求不伪造 token 数。页面显示的是 API 返回的推理文本，不是完整内部思维链。

每 10 秒上报，每 5 秒读取；隐藏网页每 30 秒读取。超过 120 秒没有 Cloud 心跳时标为过期，保留最近资料。网页读失败也保留已有内容。Cloud 已结束或离线不影响已上传资料的访问。

`?demo=1` 是明确标记的软件演示；示例数据只在浏览器使用，不写入正式研究数据。真实模拟协议验证若上报到站点，会携带 `simulation=true`，不能与能力测评合并。

## 公开资料边界

公开任务提示、锁定资源、实际请求和脱敏 SSE、模型回答、推理、工具调用／输出、worker 结束状态、最终补丁、Git 快照元数据、官方 reward 与研究报告。

仅白名单文件进入上报，**不读取／上传**官方 test.patch、grader.py、oracle、verifier XML 或可能引用隐藏源代码的完整判分 stdout。官方 reward 与判分摘要公开；原始判分日志仍完整保留在 Cloud 评测资料内。真实凭据、短期实验令牌和认证头在上传前移除，下载资料标记为脱敏记录。全文纯文本渲染，模型输出的 HTML／脚本不执行。

## 发布与复现

`dashboard/` 是官方 Sites starter 的应用源码，依赖由 package-lock.json 固定。D1 schema 在 db/schema.ts，发布前生成并审查 Drizzle migration；已应用 migration 不重写。D1 存状态与分块索引，R2 存正文，没有浏览器本地数据库冒充持久记录。

```bash
cd dashboard
npm ci
npx tsc --noEmit --incremental false
node scripts/test-api.mjs
npm run build
```

使用 Sites 工具及 site-workflow 发布；`.openai/hosting.json` 保存已有 project_id 和逻辑 DB／BUCKET，不含令牌。公开仓库为 https://github.com/Haedus-Industries/oh-my-god-deepseek 。Sites 发布使用独立托管源码 checkout，避免在研究仓库中形成嵌套 Git 仓库；部署版本和公开研究提交均记录在发布回执中。长资料保存在 R2，不进入 Git 的每次实时提交。

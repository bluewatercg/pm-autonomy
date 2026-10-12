# 需求池：把未确定的想法留在可追踪的位置

需求池已接入独立 PM 的回环服务。先建立能力会话，再从审批卡点击“打开需求池”。来源记录引用、带时区时间和 `uncertain`；不需要复制全文聊天，也不要记录秘密。负责人和优先级未确定时为 `null`。每次操作需新的 `request_id` 和当前 `expected_version`，同主体同请求重试返回缓存，改变内容或主体会冲突。

| 用户字段 | 含义 |
|---|---|
| `requirement_id / version` | 独立需求编号及乐观版本 |
| `source / goal` | 来源引用、时间、是否待确认及目标；登记后保留原始来源 |
| `notes / events` | 追加讨论及带 actor、时刻、哈希的审计历史 |
| `owner / priority / open_questions` | 可未确定的负责人、优先级、待澄清事项 |
| `status / decision_reason` | 讨论中、待决策、暂缓、已转提案、已拒绝、已被取代；暂缓和拒绝必须给理由 |
| `after_task` | 明确安排在哪些已登记任务验收闭合后再决定 |
| `ready_for_decision` | 查询时计算的决策就绪标记，不是批准、执行或提醒 |
| `superseded_by` | 指向现存需求；自环及多条需求形成的环被拒绝 |
| `proposal / task_id` | 已转换提案的 task、版本、摘要，与提案的 `source_requirement` 双向关联 |

例如负责人提出：“合成排序想法先留下，等前序任务验收后再确定。”以下是示范输入，不代表真实用户已经同意这些条目。

1. 登记 `R-SORT`，目标“讨论是否采用当前合成排序合同”，来源“本次演练需求摘要”，状态“讨论中”。保持负责人、优先级未确定。
2. 在页面选择需求，填写已有任务 ID，例如初始化实例的 `PENDING-DEMO`。写理由“先等前序验收，再决定是否采用”，点击暂缓。未知任务 ID 会被拒绝；旧引用后来缺失也保持阻塞。
3. 追加“排序、副本和空输入的验收已说明，尚待负责人确定”。可调整优先级和待解决问题；旧讨论不覆盖。
4. 前序只有客户端自报完成、任务卡被改成完成、缺失结果或缺少持久验收/治理结案时，仍然阻塞。可信闭合后，查询显示“可供决策”，状态仍为“暂缓”。不会发送通知或创建 cron。
5. 负责人确定后点击转换。页面读取并绑定当前真实提案 version/hash/scope；服务在同一 SQLite 事务中追加需求事件、登记新 task 的 `PENDING` 提案和幂等结果。需求变为“已转提案”，保留来源和全部讨论。
6. 打开待审批卡，核对实际合同、范围及验收。通过必需 architect 审查后，能力持有人另行点击批准；批准只生成回执，执行仍需单独操作。审查或容器不可用时阻塞，绝不显示为完成。

转换目前**只采用已经存在的 `history.py / select_records` 合成排序合同**，保留其真实源码摘要、基线和固定验收。自由文本目标作为 `requirement_context` 显示，不被解释为已实现功能。新的需求生成独立 task ID，继承合成执行合同；全局提案版本继续递增。当前实例仅有一个活动审批卡，已有任务执行在途时不能转换另一个需求。审批卡本身还是提案登记，不构成可派发授权；只有批准回执进入既有 broker 执行链。

依赖任务来自审批 SQLite 的持久化提案注册表。完成需要同版本提案对应的 `COMPLETED_SYNTHETIC`、`CLOSED` 且成功的持久结果，以及既有治理库中绑定该卡片的 acceptance 和 close。治理角色配置、源码或证据漂移导致核对失败时按未知阻塞。同 UID/root 管理者属于既有可信边界，不声称抵抗管理员直接改数据库。讨论写入口没有标记任务完成的 API。

## CLI 与 API

从仓库读取帮助无需启动或写入运行状态：

```sh
python3 pm/requirements_cli.py --help
python3 pm/requirements_cli.py convert --help
```

实际操作显式选择初始化后的私有运行目录与会话文件。会话 JSON 只有 `sid` 和 `csrf`，由已登记能力持有人在本地保存；不要把会话值写进命令行、仓库或公共日志。页面可直接使用，无需 CLI 会话文件。

```sh
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json list
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json read R-SORT
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json add --payload /tmp/requirement-add.json
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json note --payload /tmp/requirement-note.json
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json state --payload /tmp/requirement-state.json
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json defer --payload /tmp/requirement-defer.json
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json convert --payload /tmp/requirement-convert.json
python3 pm/requirements_cli.py --root /tmp/pm-runtime --session-file /tmp/pm-session.json archive
```

`archive` 重建 `runtime/private/requirement-archives/<requirement_id>.json` 单需求独立投影，包含完整事件，不改写 SQLite 追加历史。路径拒绝符号链接。文件不是第二份权威状态；重跑可恢复。普通 list/read 只计算就绪信息，不改变需求事件。

登记载荷示例：

```json
{"operation":"add","request_id":"demo-add-001","requirement_id":"R-SORT","expected_version":0,"source":{"ref":"演练需求摘要，非真实批准","time":"2026-10-11T12:00:00+00:00","uncertain":true},"goal":"讨论当前合成排序合同","owner":null,"priority":null,"open_questions":["何时确定进入审批？"]}
```

暂缓载荷：

```json
{"operation":"defer","request_id":"demo-defer-001","requirement_id":"R-SORT","expected_version":1,"reason":"等前序任务验收后再决定","after_task":["PENDING-DEMO"]}
```

所有写载荷都包含 `operation/request_id/requirement_id/expected_version`，其余字段严格校验，多余的 actor、批准或完成字段拒绝：

| 操作 | 额外字段 |
|---|---|
| `note` | `note` |
| `update` | `owner / priority / open_questions / after_task` |
| `state` | `status / reason / superseded_by`；不能直接设置 converted、complete 或 deferred |
| `defer` | `reason / after_task` |
| `convert` | `proposal_version / proposal_hash / scope`，从当前 `/api/card` 获取；不能凭空造批准 |

同一个回环服务提供 `GET /api/requirements`、`GET /api/requirements/<id>`、`POST /api/requirements`。复用 HttpOnly SameSite capability 会话和 CSRF/Host/Origin 校验，actor 从服务会话派生。`approval_api.py --discussion-session` 发放已由 bootstrap policy 允许的 discussion 能力，可读写讨论、暂缓和决定记录，不能转换、批准或执行。normal 可转换正常合成合同，仍须另行批准。角色名称和请求自报身份都不授予权限。这是本地能力边界，不是实名多人账户系统。

## 验证证据等级

- **公开当前可复现**：`check.py` 的 `requirements-pool` 组运行 `test_requirements.py`，含真实 HTTP、新服务进程及重启、SQLite 转换中 `os._exit(87)` 回滚、进程并发、幂等、版本、hash/replay、未知依赖、条件满足只就绪、误造完成拒绝、CSRF/Host/Origin/权限和合法对照。审批对照使用明确 synthetic reviewer，仅证明协议。
- **历史隔离候选**：见 [已测试案例](TESTED-CASES.md) 的历史摘要；不替代本次需求池测试，未自动导入私人聊天、历史任务或个人状态。
- **仅设计或待实测**：通用业务 executor、任意需求自动生成计划、真实多人账户分权、提醒、后台调度、生产激活。固定 Docker 完整执行与模型审批 gate 只有实际运行且证据完整才能称通过；源码独立模型 review 与运行时 gate 分别报告。

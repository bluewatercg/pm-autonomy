# pm-autonomy

**让项目负责人把需求变成可追踪、可控、可验收的任务，在明确授权下推进执行，并看清成果、剩余工作和下一步。**

当前状态是 **CANDIDATE / NOT_IN_FORCE**。本仓库公开了独立 PM 候选的审批、角色配置、运行时 gate、隔离执行接线、独立验收和恢复实现，以及两份项目接入 skills。当前可用写入范围仅为合成 `history.py / select_records` 演练；通用业务执行、全自动需求到代码、生产晋级和旧系统单写移交尚未接通。公开代码不会自动激活 PM 或接管业务项目。

本独立 successor 修复了恢复投影的全表覆盖缺陷：后继按 parent hash/version 只取代未完成父卡，保留完成卡、CLOSED 回执和历史依赖；漂移、治理历史丢失和不确定恢复 fail closed。需求 discussion 权限不能设置 rejected/superseded，normal/destructive 才能设置终态。SQLite 提交前不写投影，每个请求重新 load_store。治理 checkpoint 的 seq/hash 及导出验证共用 SQLite 一致读快照；先读 checkpoint 再开启读快照，写入提交并释放数据库锁后才获取 checkpoint 文件锁。确定性并发对照与删除/截断拒绝回归见[当前测试摘要](docs/evidence/requirements-pool.json)。当前源码未做浏览器渲染验证，旧浏览器结果仅归属历史源码。

## 它怎样帮助负责人处理项目

项目容易出现的问题是：口头需求没有验收标准、任务与原需求脱节、模型自称完成却没有证据、修改范围不断扩大，以及旧任务关闭后无人承接剩余项。本项目的目的，是把这些决定和证据放到能核对的任务流程里。

负责人关注需求、方案、授权范围和验收结果；执行过程记录任务版本、实际 Git 基线、产物、失败及恢复。模型可以提出建议，必需的批准、审查和验收由各自入口承担。当前合成运行时已经实现其中的控制机制，业务项目接入仍需补齐并验证执行适配。

适合想评估 AI 项目治理的项目负责人、开发者和工具维护者：先阅读流程，再在 Linux/WSL 的独立目录验证合成任务。也可以用附带 skills 整理新项目或历史项目的接入提案。

不适合直接用于生产自动接管，或期待输入任意业务需求就自动改代码、部署服务的使用者。现有实现没有通用项目执行器，没有账户级多人分权，也没有完成公开环境的 Docker 闭环和真实模型审查验收。

## 用户实际怎么用

下面是负责人处理业务项目的目标工作流程；其中的分页例子用于解释需求与任务关系，**不是当前已经支持的业务功能**。本仓库实际可运行的卡片仍是后文的合成排序任务。

1. **提出需求。** 例如：“已有订单查询保留现有筛选和排序，再加分页。”同时说明页大小、越界行为和验收样例。标准不清楚时先澄清，不猜测。
2. **查看任务卡。** 核对目标、范围、基线、允许动作、验收项、风险、回退和剩余事项。大型方案先完成独立 architect 审查。
3. **批准、修改或驳回。** 批准明确方案与范围；修改写出需要调整的点，修订后重新核对；驳回则停止该提案。会话确认本身不构成批准。
4. **批准后执行。** 当前页面还需单独点击执行。原授权范围内的常规可逆步骤沿用已有授权，无需逐动作询问；超出范围、破坏性变更或生产动作需要对应授权。
5. **看验收与剩余。** 核对独立证据是否满足承诺，失败则在原承诺内返工。执行成功、审查 PASS 和用户批准各自记录，不能互相替代。
6. **回顾并优化。** 说明已验收、未完成和已转交项。后续优化建立关联的新任务，保留旧成果与旧证据。

在这个例子中，已有查询是已验收基线，加分页属于保留既有行为的增量优化。验收既检查分页，也检查原筛选和排序没有回退。若第一页正确但越界处理未交付，应分别记录成果与剩余，不能笼统写“全部完成”。

当前自然语言 intake 有结构化分类 `FIRST / OPTIMIZATION / AMBIGUOUS / DESTRUCTIVE`：已有已验收上下文且保留行为的增量需求应为 `OPTIMIZATION`；不清楚则暂停澄清；移除或反转已验收合同走破坏性分支。这套 schema **限定合成 `history.py` scope**，分类结果不会自动变成通用业务审批卡或代码执行计划。

### 已实现的需求池

聊过但还没确定的想法可以先登记，再讨论、暂缓或拒绝。打开审批服务并确认能力会话后，点击“打开需求池”；页面支持追加讨论、负责人/优先级、待解决问题、安排在已登记任务验收闭合之后，以及待决策/暂缓筛选。来源只保存摘要引用、带时区时间和待确认标记，不导入完整聊天或猜测历史批准。

条件满足只显示 `ready_for_decision`，不会提醒、批准或执行。确定后转换成 **PENDING 待审批提案**，保留需求历史并建立双向来源链接；当前只绑定已有 `history.py` 合成排序合同，不能将任意业务想法自动变成执行计划。讨论会话不具有转换、批准或执行能力。

[需求池完整示例、CLI/API 与证据等级](docs/REQUIREMENTS-POOL.md)说明“想法 → 暂缓 after_task → 讨论 → 待审批 → 批准”的实际操作。`python3 pm/requirements_cli.py --help` 可安全查看 read/list/add/note/update/state/defer/convert/archive；`check.py` 已包含真实 HTTP/SQLite/新进程并发与中断恢复测试。运行数据和单需求 archive 投影只留私有 runtime。

### feature、task、run 和关系

以下是接入 skills 的档案约定，当前运行时没有完整通用 feature/task/run 管理接口或关系图执行引擎。

| 概念 | 负责人怎样理解 |
|---|---|
| feature | 长期产品归属，例如“订单查询” |
| task | 一次边界清楚、有验收标准的交付承诺，例如“保留查询行为并添加分页” |
| run | 某个 task 的一次执行尝试，记录结果、失败和证据 |

原承诺内的正常返工保留同一个 task，追加新的 run；任务版本和授权变化仍须按绑定规则重新核对。已验收后的新增优化建立新 task，避免重写旧承诺。关系从当前任务指向关联对象：

| 关系 | 方向与含义 |
|---|---|
| `improves` | 新优化任务 -> 被优化的已验收任务 |
| `fixes` | 修复任务 -> 被修复的任务或成果记录 |
| `depends_on` | 当前任务 -> 必须先满足的依赖任务 |
| `takes_over` | 后继任务 -> 被承接未完成项的原任务 |

部分交付按验收项记录 `accepted / remaining / transferred`。转交应绑定后继、责任和剩余验收标准；依照结案规则明确转交后，原 task 可以关闭，但 `closed` 不表示所有功能都已完成。当前页面的 `COMPLETED_SYNTHETIC` 只表示合成合同的完成状态，不能推断业务项目整体完成。

## 运行合成候选

### 环境与依赖

运行环境为 **Linux 或 WSL、Python 3.10+、Git**。Python 控制实现使用标准库，无需安装 pip 包。Windows 原生环境缺少 `fcntl`、Linux `SO_PEERCRED` 和跨 UID 机制，不能运行完整流程。

Node 用于 `check.py` 中的页面逻辑检查，缺少时该项 BLOCKED。Docker daemon 与本机固定镜像用于隔离执行和容器测试。真实模型建议与审查还需部署者自行准备兼容下述 argv 接口的 OMP 入口、provider 配置及模型观测；仓库没有自动安装这些依赖的脚本。

### 初始化与打开审批卡

在仓库根目录执行以下命令。两个目标目录都必须不存在；需要重跑时换一个新的目录名。运行状态与原始证据保存在仓库外。

```sh
python3 pm/check.py --report-dir /tmp/pm-autonomy-check
python3 pm/initialize.py --destination /tmp/pm-autonomy-runtime
```

`check.py` 建立全新临时副本，协议检查使用 synthetic fixture，不调用真实模型。缺少本机固定镜像时，Docker 项记录 `BLOCKED / PINNED_IMAGE_UNAVAILABLE_NO_PULL`，不会自动拉取镜像。它的退出码只依据 FAIL 判断；退出码为 0 也可能含 BLOCKED，必须读报告中的 `summary.json`。

`initialize.py` 生成随机本地密钥、私有状态目录、真实 synthetic Git baseline 和待审批提案，不导入用户业务或旧 Git 历史。普通初始化不要加 `--fixture`；该选项专供测试，不能用合成审查替代真实任务审查。

```sh
cd /tmp/pm-autonomy-runtime
python3 approval_api.py
```

服务绑定本机回环地址和动态端口。打开终端输出的限时 capability 链接，按页面确认能力持有人并建立会话，然后查看任务卡。链接本身是凭据，不共享、不提交、不写入公共日志；Ctrl+C 停止本次服务。不要直接打开 HTML 文件期待审批生效，离线预览的操作按钮禁用。

初始化任务为 `PENDING`，内容是“合成 history.py 按 id 升序返回副本”，固定验收包括排序、返回副本和空输入。页面展示完整提案 JSON，包括 scope、base、actions、验收和剩余事项。

| 页面操作或状态 | 实际效果 |
|---|---|
| 确认会话 | 证明持有 capability，任务仍待审批 |
| 批准 | 审查及基线检查通过后生成绑定回执，进入 `APPROVED_EXECUTION_PENDING` |
| 修改 | 必须填备注；同 task 建立后继版本，撤销旧授权，标记 `revision_required` |
| 驳回 | 标记 `REJECTED`，不执行提案 |
| 执行 | 使用批准的 request，通过 broker 派发，再核验独立证据和结案 gate |
| `EXECUTION_BLOCKED` | 受影响流程未完成；查看回执中的原因和持久证据 |

**修改备注不会自动改方案或代码。** 当前没有通用自动修订入口；待修订版本不能直接批准，维护者需通过经核实的候选修订流程完成修订和重新绑定，不能直接篡改旧回执或旧证据。

空模型配置允许查看卡片和建立会话，但审批被 `BLOCKED_MODEL_NOT_CONFIGURED` 阻塞，不能进入合法执行。批准后缺少镜像或必需审查也会阻塞。执行结果已持久化而结案审查缺失时，补齐审查后用同一主体、版本和 request 恢复；结果未知则人工调查，禁止盲目重派。

可在 Linux/WSL 核对实际 CLI 参数：

```sh
python3 pm/initialize.py --help
python3 pm/check.py --help
python3 pm/approval_api.py --help
```

`approval_api.py` 提供 `--port`、`--open-browser`、`--discussion-session` 和 `--destructive-session`。默认 bootstrap policy 允许 `normal` 和仅需求讨论的 `discussion`；破坏性能力仍须另行配置，启动参数不能扩权。

### 模型配置与建议入口

角色与模型品牌不绑定。职责、思考强度和能力从本地配置加载；可以复用同一已核实模型承担多个角色，但职责、进程和证据分开。模型名称或角色配置不授予批准权。

| 角色 | 职责 |
|---|---|
| `pm` | 组织需求与方案，提出审批请求 |
| `explorer` | 阅读项目材料，说明结构与现状 |
| `researcher` | 研究方案、约束和备选路径 |
| `worker` | 提出实现建议；真实写执行必须走已批准 broker |
| `architect` | 独立检查方案、重复错误和长任务结案 |
| `verifier` | 分析证据；权威验收由独立确定性进程执行 |

在生成的运行目录中编辑 `roles.json`，填写六个角色的实际模型 ID。将已观测 ID 同时登记到 `model_policy.verified_models` 和 `model-id-observation.json.models`；`basis` 留存本地观测依据，实际配置保持 `fixture: false`。不要用猜测 ID 或 fixture 冒充已核实模型。备用模型必须显式配置且已观测，配置漂移会使旧审查绑定失效。

启动模型相关入口前，由部署者在本地环境提供下表变量。表中只是接口说明，仓库不包含可用凭据，也不提供通用 provider 安装命令。

| 变量 | 用途 |
|---|---|
| `PM_MODEL_EXECUTABLE` | OMP 可执行文件的本地绝对路径 |
| `PM_MODEL_CONFIG` | 仓库外 provider 配置文件的本地绝对路径 |
| `PM_MODEL_AGENT_DIR` | 可选的独立 OMP 数据目录 |
| `PM_MODEL` | kernel intake 使用的已核实模型 ID |
| `PM_CONTAINER_IMAGE` | 本机已有镜像的 image ID，严格为 `sha256:` + 64 位十六进制摘要 |

[模型 adapter](pm/model_adapter.py) 使用 argv 直接执行独立 OMP 进程，禁用模型工具；不会 source 私有脚本或加载内置 provider 配置。缺少入口、模型或配置时明确 BLOCKED；路径存在不证明 provider 可用。输出必须是 OMP NDJSON 中的结构化 assistant JSON，schema 不合格则停止。

准备好本地材料文件 `request.txt` 后，在运行目录请求只读建议：

```sh
python3 role_dispatch.py --role explorer --prompt-file request.txt
```

其他非 architect 角色同理，输出权限为 `ADVISORY_ONLY`，不会自动批准、写业务代码或完成验收。architect 必须通过绑定审查入口。

合成需求分类入口在运行目录执行：

```sh
python3 next_kernel_v3.py intake --root . --spec intake-request.json
```

`intake-request.json` 由操作者在私有运行目录创建，例如：

```json
{
  "request": "demo-intake-001",
  "requirement": "在 history.py 中实现 select_records，按 id 升序返回各条记录的副本，空输入返回空列表",
  "context": ""
}
```

已有已验收行为时，在 `context` 描述真实基线。结果保存在运行目录的 `intake/<request>/proposal.json`，仍需审批和执行接线；不要调用前代 `broker` 模式绕过当前审批入口。

### architect 的三处硬 gate

在运行目录执行对应审查命令；仅在真实模型配置可用时调用，不使用 synthetic reviewer 代替。

```sh
python3 review_task.py --trigger large_plan_before_approval
python3 review_task.py --trigger repeated_similar_error
python3 review_task.py --trigger long_task_closeout
```

大型方案在批准前需要 PASS；同类错误达到第二次要求重复错误审查；长任务结案需要绑定已完成独立验收的结案审查。三个触发按提案的治理 policy 生效，默认演练启用了大型方案、长任务及阈值 2。它们是运行时阻塞检查，缺失、FAIL、TIMEOUT、过期或版本/摘要漂移均阻止相应步骤。

**review != 独立验收 != 用户批准。** 架构审查评价绑定方案与证据，独立验收复算实际产物和测试，用户批准授予具体范围内的执行权限。结案审查完成后仍需通过结案 gate；review 命令本身不会批准或执行任务。

### Docker 边界

执行器要求本机准备可信 Linux 镜像，包含 `/usr/local/bin/python3` 和 Git。[Dockerfile](Dockerfile) 提供配方，基础 tag 并非不可变构建来源；实际运行必须核实本机 image ID 并固定到 `PM_CONTAINER_IMAGE`。公开验证没有构建、安装或拉取镜像，配方和容器闭环未在公开环境验收。

准备镜像后可使用 Docker 的真实查询命令，将输出的 ID 在本地设置到环境，再重新运行完整 `check.py`；不要填一个不存在的摘要。

```sh
docker image inspect --format '{{.Id}}' pm-autonomy-local:checked
```

这里的 tag 是部署者自行准备并核实的本机镜像名称，不表示仓库已提供该镜像。容器限制为无网络、只读 rootfs、无宿主挂载、仅 tmpfs、无端口、非 privileged。编排进程使用 root 建立不同测试 UID，broker、执行者和非法请求者分属不同 UID；不能据此声称抵抗宿主或 Docker 管理者。

## 常见案例与测试入口

[已测试案例与能力矩阵](docs/TESTED-CASES.md)逐案例列出用户问题、输入前置、PM 行为、观察结果、验收标准、实际测试版本、复现命令和不证明的边界。证据分为 **公开当前版本可复现 / 原隔离候选已跑但未等价迁移 / 仅设计或待实测**；不会用历史成功代替公开当前验收。

| 负责人常见问题 | 已有案例或观察 | 当前支持到哪一步 |
|---|---|---|
| 首次交付后如何继续优化？ | 真实模型四任务：排序 → 名称筛选 → limit 部分交付 → 校验承接 | 历史隔离合成实例通过；拆解和关系预定义，未迁移为通用业务执行器 |
| 剩余项如何不丢，失败如何返工？ | T3 A2 转交 T4；T4 R1 拒绝后追加 R2；公开异常登记 8/8 | 历史任务档案与公开异常机制分别有依据，不把 closed 当全部完成 |
| 歧义、反转合同、自称批准怎么办？ | 历史 v5 分类及合同检查 18/18；过期、scope、drift 拒绝 | 窄 scope 的历史分类部分支持；公开审批绑定可复现，非任意需求语义保证 |
| 秘密能隔离，审查能跳过吗？ | 历史 cross-UID 9/9；公开三 architect gate 25/25 | 历史容器不抵抗管理员；公开 gate 使用 synthetic reviewer |
| 中断后会重复执行吗？ | 真实进程中断恢复、同 request 缓存、unknown 阻塞 | 已列边界可复现；断电与外部 provider exactly-once 未证明 |
| 卡片是否等于真人批准、能直接部署吗？ | human 仍 PENDING；公开无模型审批 BLOCKED、outbox=0；Docker BLOCKED | 卡片协议可核对；真人审批、完整模型/容器闭环、生产仍待实测 |

[公开测试白名单摘要](docs/evidence/public-current.json)给出实际测试提交与当前源码一致性、分层计数和 BLOCKED；[历史候选脱敏摘要](docs/evidence/historical-candidates.json)保留各期失败与源码快照摘要。原始私人报告、配置、日志和凭据没有上传。订单分页仍仅为说明性例子，不能当已支持业务。复现请使用案例文档的 R1（公开分层检查）、R2（只读历史摘要）或 R3（新的待审批卡），先确认各自前置和边界。

## 当前验证到哪一层

首个公开候选验证对应公开基线 `6e339a8`，来自该公开副本的实际测试输出。以下计数属于不同层，不能相加成独立场景总数，也不能用前序内部版本的“30 个测试”等计数替代。

| 验证层 | 公开候选的已记录结果 | 证明范围 |
|---|---|---|
| Python protocol / boundary | 34 tests，OK | 协议、授权边界、持久化及实际进程中断恢复 |
| architect gates | 25 passed，0 failed | synthetic reviewer 下三触发及漂移阻塞 |
| exception recovery | 8 passed，0 failed | 异常登记、恢复与去重 |
| Node VM card logic | 9 checks，pass=true | 页面逻辑；`browserRenderingTested=false` |
| 未配置模型的回环 HTTP | card=200、PENDING、approval BLOCKED、outbox=0 | 能查看卡片但不能绕过模型配置批准 |
| Docker governed flow / regression | BLOCKED：固定镜像不可用，未拉取 | 容器端到端尚未通过 |

报告汇总为 **5 个 PASS 运行组、0 FAIL、1 个 BLOCKED 组**，是运行组计数，不是上表测试项的总和。Docker daemon 可用不等于指定镜像可用；协议 PASS 不替代容器验收。没有执行真实外部模型审查，也没有浏览器视觉渲染验收。README 文档更新不代表重新跑过运行时代码测试。

## 新项目与历史项目接入

新项目可按[新项目 skill](skills/pm-new-project-onboarding/SKILL.md)、[接入方案](skills/pm-new-project-onboarding/references/PLAN.md)和[空检查单](skills/pm-new-project-onboarding/assets/checklist.md)整理负责人、环境、授权范围、模型角色、任务档案和首个待审批任务。

已有业务、服务、任务台账或旧 PM 的项目，按[历史项目 skill](skills/pm-existing-project-integration/SKILL.md)、[集成方案](skills/pm-existing-project-integration/references/PLAN.md)和[空检查单](skills/pm-existing-project-integration/assets/checklist.md)先做只读盘点，保留历史、标注未知状态，再提出隔离试点和移交计划。

这些文件是接入工作流程，**不是已实现的业务导入器、shadow 运行调度器或可信单写租约**。旧系统 handoff 尚未支持；未证明旧写入口停写、没有可信移交机制时不能启用新写入口。当前初始化只建立 synthetic repo。

生产使用还需要账户认证与分权、通用业务适配、真实模型和容器验收，以及分别授权的 dev/staging/prod 晋级。本候选保持 `NOT_IN_FORCE`，开发授权、验收和公开发布代码不包含服务重启、生产部署、账户或数据迁移。

## 安全、恢复与源码导航

capability 会话只证明持有人，不是平台账户认证、实名认证或操作者为人类的证明。同 UID、root、SSH 和 Docker 管理者属于可信管理边界，可以修改私有状态。生产环境需要独立身份、审批和执行分权，不能把本地 capability 演练当作已实现的多人权限系统。

初始化在本地生成随机 HMAC 密钥，没有默认密钥。运行目录中的密钥、capability/session、SQLite、模型配置和原始日志都应保持私有，不要 commit。synthetic 批准及 reviewer 的 PASS/FAIL/TIMEOUT 只是 TEST fixture，不是真实审查或用户批准。

批准回执绑定 task、version、方案摘要、scope、base、artifact、actions、expiry 和 subject。成功派发先持久化结果，再走独立验收和结案 gate；同主体/版本/request 恢复已有结果不重派。已有 intent 但结果未知时阻塞并调查，不承诺外部模型或断电情况下的 exactly-once。

| 文件 | 阅读用途 |
|---|---|
| [PM_MANIFEST.yaml](PM_MANIFEST.yaml)、[RULES.md](RULES.md) | 候选状态、支持范围与约束 |
| [initialize.py](pm/initialize.py)、[check.py](pm/check.py) | 初始化和分层检查 |
| [approval_api.py](pm/approval_api.py)、[remote_store.py](pm/remote_store.py)、[card.js](pm/card.js) | 卡片、持久状态、批准与执行入口 |
| [roles_gate.py](pm/roles_gate.py)、[review_task.py](pm/review_task.py)、[role_dispatch.py](pm/role_dispatch.py) | 角色建议与绑定审查 |
| [bridge.py](pm/bridge.py)、[container_task.py](pm/container_task.py) | 验签、基线与固定容器派发 |
| [secured_kernel.py](pm/secured_kernel.py)、[governed_kernel.py](pm/governed_kernel.py) | UID 拒绝、幂等恢复与治理结案 |
| [next_kernel_v3.py](pm/next_kernel_v3.py) | 合成 intake 与前代内核；不作为绕过审批的入口 |
| [verify_acceptance.py](pm/verify_acceptance.py) | 独立复算产物、原始输出、隔离元数据及事件链 |
| [角色模板](examples/roles.json)、[模型观测模板](examples/model-id-observation.json) | 无凭据的本地配置起点 |
| [PUBLIC_FILES.json](PUBLIC_FILES.json)、[audit_public.py](pm/audit_public.py) | 公共文件白名单与计数扫描 |

## 反馈与后续方向

反馈请说明期望、实际状态或阻塞码、复现步骤及基线版本；附上脱敏后的最小材料，不上传凭据、完整私有日志或用户业务数据。可在本仓库 Issues 提交。

后续优先补齐真实模型与固定容器闭环、通用任务修订和业务执行适配，再验证历史项目单写移交与生产分权/晋级。每项都以可核验入口和证据更新支持状态，不把 roadmap 当作当前能力。

本仓库没有选择或附加软件 license；公开可见性不自动授予开源许可。

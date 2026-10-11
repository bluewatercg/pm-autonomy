# pm-autonomy

独立 PM 管理候选项目，状态 **CANDIDATE / NOT_IN_FORCE**。本仓库包含运行时源码、规则、审批页面/API、角色派发、architect 三触发、可信 UID broker、独立验收、恢复测试及两份接入 skills。公开代码不会自动激活 PM，也不会接管任何既有业务项目。

这是可独立运行的治理候选与 `history.py / select_records` 合成任务实现。当前写入 scope 和固定验收合同有意限定于此演练；任意业务项目自动接管、任意语言工具执行、生产部署、旧系统单写租约和全自动需求到代码闭环尚未实现。本项目不是完整可部署的通用生产 PM。公开了完整候选控制链，不把 skills 的流程说明当作已经实现的运行时功能。

## 快速开始

运行环境：Linux 或 WSL，Python 3.10+ 与 Git；标准库实现，无需 pip 安装。Windows 原生环境缺少 `fcntl`、Linux `SO_PEERCRED`、跨 UID 等机制，请在 Linux/WSL 执行。Node 只用于页面逻辑测试；Docker 只用于容器执行测试。

以下命令从仓库根目录执行。目标目录必须不存在，初始化拒绝覆盖。所有生成状态放在仓库之外。

```sh
python3 pm/check.py --report-dir /tmp/pm-autonomy-check
python3 pm/initialize.py --destination /tmp/pm-autonomy-runtime
```

`check.py` 每次建立全新临时运行副本，输出真实测试结果到指定私有报告目录。合成模型只用于协议 fixture，不调用真实模型、不代表人类批准。未配置本地固定容器镜像时，Docker 测试会明确输出 `BLOCKED: PINNED_IMAGE_UNAVAILABLE_NO_PULL`；通过的协议测试不能替代 Docker 验收。

```sh
cd /tmp/pm-autonomy-runtime
python3 approval_api.py
```

服务绑定 `127.0.0.1` 动态端口，终端输出限时 capability 链接。此链接是凭据，只供本地操作者打开，不应共享、提交或记录到公共日志。服务由 Ctrl+C 停止。初始化后任务为 PENDING；确认会话不等于批准，批准后仍须另点执行。空模型配置允许查看卡片和建立能力会话，但审批/执行会明确阻塞 `BLOCKED_MODEL_NOT_CONFIGURED`，不会绕过必需审查。

首个公开候选验证：34 个 Python 协议/边界测试、25 个 architect gate 场景、8 个异常恢复场景、9 项 Node VM 页面逻辑和未配置模型的真实回环 HTTP 阻塞检查通过。Docker daemon 可用，但所需独立固定镜像未提供；容器端到端测试 BLOCKED。没有执行真实外部模型，没有浏览器视觉渲染验收。报告中的层级计数不相加为一个场景总数。

## 外部模型接入

`pm/model_adapter.py` 保留 OMP 独立进程调用和工具禁用参数，用 argv 直接执行，不 source 私有代理脚本、不读取内置 provider 配置。OMP 与 provider 配置由部署者单独准备；仓库不附带凭据，也不自动安装或调用模型。未设置入口、模型或配置时返回明确 BLOCKED。

在生成的运行目录中，将 `roles.json` 六个角色的 `model` 填为部署者实际核实的模型 ID，并在 `model_policy.verified_models` 及 `model-id-observation.json.models` 登记同一实际观测 ID。`basis` 保存本地真实观测依据；不要用 fixture 或猜测 ID 冒充已核实模型。备用模型须显式配置且已核实。配置发生变化后旧审查绑定失效，须新候选/后继重新审查。

环境接口如下；值均由部署者提供，空示例不包含任何可用凭据或私有路径。

| 变量 | 用途 |
|---|---|
| `PM_MODEL_EXECUTABLE` | OMP 可执行文件绝对路径 |
| `PM_MODEL_CONFIG` | 仓库外 provider 配置文件绝对路径 |
| `PM_MODEL_AGENT_DIR` | 可选的独立 OMP 数据目录 |
| `PM_MODEL` | kernel intake 的已核实模型 ID |
| `PM_CONTAINER_IMAGE` | 本机已存在的容器 image ID，严格为 `sha256:` + 64 位摘要 |

代理如有必要由部署者在启动环境显式配置；本项目没有私有 IP、SSH alias 或代理配置默认值。模型输出须为 OMP NDJSON 中的结构化 assistant JSON；schema 校验失败会阻塞。模型不取得批准能力，所有模型工具调用禁用。

真实审查入口（需上述外部配置可用；本次未执行真实模型）在运行目录执行：

```sh
python3 review_task.py --trigger large_plan_before_approval
python3 review_task.py --trigger repeated_similar_error
python3 review_task.py --trigger long_task_closeout
```

`large_plan_before_approval` 必须在大方案批准前 PASS；同类错误达到 2 次要求 recurrence 审查；长任务结案前要求绑定已完成独立验收的 closeout 审查。缺失、FAIL、TIMEOUT、过期和版本/摘要漂移全部阻塞。review 不是批准，也不是验收。角色建议派发入口为 `python3 role_dispatch.py --role explorer --prompt-file <本地材料文件>`；其他非 architect 角色同理，仅生成建议。worker 的真实写执行只能走批准后的 broker；verifier 的权威验收由独立确定性进程执行。

## 目录与实现

| 目录/模块 | 能力 |
|---|---|
| `RULES.md`, `PM_MANIFEST.yaml` | 独立候选身份、执行/验收/晋级边界及实际支持状态 |
| `pm/initialize.py`, `pm/check.py` | 全新运行目录、真实合成 Git baseline、隔离验收 |
| `pm/approval_api.py`, `pm/remote_store.py` | 回环 HTTP、SQLite 持久化、能力会话、CSRF/Host/Origin、signed outbox |
| `pm/approval-card.html`, `pm/card.js` | 提案显示、approve/change/reject、批准后独立执行 |
| `pm/roles_gate.py`, `pm/role_dispatch.py`, `pm/reviewer_process.py` | 六角色模型/effort/能力配置、独立建议派发、三触发硬 gate 与 review 绑定 |
| `pm/bridge.py`, `pm/container_task.py` | 验签、实际 Git base、固定镜像无网络容器、跨 UID broker 接线 |
| `pm/next_kernel_v3.py` | intake 与历史基线 kernel；保留前代实现用于绑定及兼容场景 |
| `pm/secured_kernel.py`, `pm/governed_kernel.py` | UID 在读取请求前拒绝、持久幂等与恢复；治理版本延迟结案 |
| `pm/verify_acceptance.py` | 独立进程复算 dispatch、原始 stdout/stderr、隔离元数据、Git base、固定测试和事件链 |
| `pm/test_*.py`, `pm/test_card.js`, `pm/run_validation.py` | 协议、拒绝对照、三触发、异常去重、实际中断恢复及可选 Docker 回归 |
| `examples/` | 空角色/模型观测模板及仅 normal 的 bootstrap policy，无密钥 |
| `skills/pm-new-project-onboarding/` | 新项目接入流程、方案、空检查单 |
| `skills/pm-existing-project-integration/` | 历史项目只读盘点、影子试点、单写移交/回退流程 |
| `PUBLIC_FILES.json`, `pm/audit_public.py` | 精确公共文件白名单、文件及新 Git 历史计数扫描 |

三份 kernel 保留候选版本及对应测试语义。审批 runtime 使用 secured/governed 路径；不要直接使用前代 `next_kernel_v3.Broker` 代替审批入口。独立初始化仅建立 synthetic Git repo，不导入用户业务或旧 Git 历史。

## 容器范围

执行器需要部署者准备固定、可信、含 `/usr/local/bin/python3` 和 Git 的 Linux 镜像。`Dockerfile` 提供可复建配方，本次没有运行构建、安装或镜像拉取；因此配方与容器闭环在公开环境未验收。部署者构建并核实后应使用实际 `docker image inspect` 输出的 image ID 设置 `PM_CONTAINER_IMAGE`，再运行完整 `check.py`。

容器限制为 network=none、read-only rootfs、无宿主挂载、仅 tmpfs、无端口、非 privileged。容器编排进程为 root 以建立三个测试 UID；broker/执行者/非法请求者分属不同 UID。额外能力只用于该隔离编排，并不声称隔绝 Docker 或宿主管理者。

## 安全、恢复及状态

批准回执绑定 task/version/proposal hash/scope/base/artifact/actions/expiry/subject；change 生成需修订后继、撤销旧授权，不把备注变成代码修复。成功派发先持久化结果再走 close gate；同一主体/版本/request 可恢复已有结果，不重派。intent 有记录但 result 未知时阻断，需人工调查；不声称外部模型或断电下 exactly-once。

能力会话只证明持有人，不是平台账户认证、实名认证或操作者为人类的证明。同 UID、SSH/root 和 Docker 管理者属于可信管理边界，可修改私有状态。测试中合成批准和 PASS/FAIL/TIMEOUT reviewer 都是明确 fixture，仅 TEST 任务允许 synthetic review；不是真实审查或用户审批。

公开树不包含 raw flows、私有目录、密钥/能力/session、带值凭据配置、SQLite、Git objects/bundle、压缩包、内部证据、记忆或用户业务数据。运行目录与全部模型/测试原始日志都应保持私有。初始化用运行时随机密钥，不提供默认 HMAC key。

当前 `NOT_IN_FORCE` 永久作为本候选声明；开发授权不含生产部署、重启、账户/数据迁移或运行时激活。任何部署/接管必须独立授权和验收。本仓库没有选择或附加软件 license；公开可见性不自动授予开源许可。

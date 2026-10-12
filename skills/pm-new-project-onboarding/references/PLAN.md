# 新项目接入方案

## 阶段与产物

| 阶段 | 输入 | 产物 | 通过条件 | 阻塞条件 |
|---|---|---|---|---|
| 0 现场探测 | 项目目录、运行时、身份说明 | inventory、能力矩阵 | 实际路径和能力有证据 | 凭据缺失、身份未知、端口冲突 |
| 1 设计 | 负责人确认、禁止范围 | constitution、规则源、角色映射 | 每条硬规则有失败检查 | 只有散文规则 |
| 2 初始化 | 空项目Git基线 | 任务档案、事件流、状态投影 | append-only、版本和摘要可复算 | 写入口不唯一 |
| 3 角色与审批 | 角色/模型候选、审批主体 | role config、待审批卡、授权信封 | task/version/scope/base/actions/expiry绑定 | 自报角色或伪造批准 |
| 4 隔离演练 | 合成任务 | worktree、测试、故障证据 | 未授权拒绝、合法执行、恢复幂等 | 生产挂载、无法回放 |
| 5 architect触发 | 大方案、重复错误、结案请求 | 审查记录 | 三触发均硬阻塞缺审查 | 失败静默跳过 |
| 6 首个真实任务 | 用户审批、完整卡片 | run、独立验收、结案或转交 | 用户选择已记录且验收绑定固定产物 | 自动批准、验收与执行同一身份 |
| 7 激活评估 | 全部报告和残留 | 激活裁决 | 单独得到激活授权 | 仍有PARTIAL/BLOCKED关键能力 |

## 任务与关系

feature 是长期产品归属；task 是边界清晰的交付承诺；run 是一次执行尝试。正常返工追加同task的新run。已验收成果的优化新建task并记录 `improves`；修复用 `fixes`；依赖用 `depends_on`；接替未完成项用 `takes_over`。部分交付必须逐验收项记录 accepted、remaining、transferred。

## 审批卡最小字段

`task_id`、来源、目标、proposal_version、proposal_sha、scope、base_head、worktree、环境、actions、expiry、验收项、风险、禁止范围、恢复方案、审批主体摘要。破坏性变更单独分类和能力，不允许普通批准升级。

## 发布和恢复

dev、staging、prod逐级授权；开发批准不含生产发布。中断后按事件和请求状态恢复；外部调用结果不明标记 `blocked_unknown`，不盲目重发。证据冻结后建successor，禁止就地改写。

## 融合控制验收
初始化保持 L1；L0 需求写入拒绝，L1 协作成功且零派发。升级 L2 由负责人 normal 能力在 API/UI 或 CLI 明确操作，观察与控制 scope 分开，绑定 synthetic-history、history.py、dev / isolated synthetic Docker、现存 task、完整 session actor。配置不替代审批。L3 流程或通用项目 adapter 缺失必须 BLOCKED；档位不授予生产权限。用真实 HTTP/SQLite/新进程验证暂停先赢时零启动、启动先赢时保留 RUNNING/UNKNOWN、关键写入与暂停事务 fence、旧 epoch 回执拒绝、重启暂停持久和本地独立停止。停止 kill/inspect 失败先持久 UNKNOWN，再有界释放 fence；旧 permit 未确认结束时 pause 返回 dispatch_stopped=true、write_quiescent=false、handoff_required=true，不能称干净暂停。每次 broker begin/verify 检查融合三表、非空完整初始事件，缺失零 permit、不重建；recover 只恢复已有结果，QUEUED 返回 RECOVERY_NOT_STARTED，未知不盲重派。

## 能力边界

能力会话不等于实名或人类身份；同UID、容器管理员和宿主管理员边界单独记录。模型名必须现场核实，配置存在不等于调用成功。模板字段是方案示例，不是某个产品的运行时schema。

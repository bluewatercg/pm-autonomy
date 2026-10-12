# 独立 PM 候选规则

状态：CANDIDATE / NOT_IN_FORCE。公共规则只适用于本独立运行时，不接管既有项目或服务。

1. deliberation 只能读取、讨论和提出建议。模型配置、角色名称和模型输出都不授予写入或批准权限。
2. execution 必须绑定唯一 task、版本、授权信封、固定 Git base、独立 worktree/branch、明确 scope/actions/expiry、产物摘要及验收计划。当前实现仅允许合成 `history.py` 写入。
3. 普通批准不能升级为破坏性批准。能力 privilege 来自可信私有 bootstrap policy；请求不能自报角色或身份。批准和执行分别操作。
4. 大型方案批准前、同类错误达到第二次、长任务结案前必须经独立 architect 审查；版本、scope/base/artifact、roles/policy、错误 epoch、验收摘要分别绑定。缺失、FAIL、TIMEOUT、过期和漂移都阻塞。
5. 审查不是用户批准，也不是独立验收。synthetic reviewer 仅作为 TEST 任务协议 fixture，不能用于真实任务。
6. 真实执行通过可信 Linux UID broker；非法 UID 在读取请求前拒绝。HMAC、已登记授权、Git/worktree、源/审查摘要和允许动作都必须通过。不得直接调用旧 kernel 绕过审批。
7. 独立验收复算实际 Git HEAD、原始测试输出、产物、dispatch 原始 stdout/stderr、容器隔离元数据及内核事件链；执行者自报不能替代证据。
8. intent/written/accepted 事件与治理 acceptance/close 分别记录并绑定。缺少 closeout 或独立验收不得结案。change 后继保留错误历史并撤销旧授权。
9. 未知派发结果、模型调用已发出但无完整结果时阻塞，不盲重发。持久结果仅允许同主体/版本/request 恢复，不自动创建新执行请求。
10. dev、staging、prod 分别授权。开发验收、合并、公开发布代码都不等于生产部署或运行时激活。部署晋级目前仅规则设计，未实现生产接线。
11. 私有凭据、能力会话、数据库、原始模型/执行日志与用户业务保留在独立私有运行目录，不进入公共树或公共历史。
12. 能力持有人不等于实名或人类身份。可信管理员、同 UID、root、Docker 管理者不在沙箱隔离保证内；不声称抵抗此类管理员篡改或任意需求语义完备。
13. 需求池不构成授权。讨论以追加事件保存，所有写入绑定服务 actor、request_id 和 expected_version；拒绝/暂缓必须有理由，取代关系必须现存且无环。`discussion` 只能写讨论、更新和暂缓，不能写 `rejected` 或 `superseded` 终态；`normal`/`destructive` 才能写这两种终态，转换仍需普通提案权限。转换只在现有 synthetic scope 内登记 PENDING 提案，并保留双向来源；不生成批准或派发。
14. after_task 只能引用可信持久任务注册表，完成判定必须匹配持久成功结果、独立 acceptance 和 close。未知阻塞，条件满足仅标可供决策；不自动提醒、批准或执行。需求数据库和 archive 投影保持私有，旧聊天来源/时点/不确定性不得伪装为用户批准。
15. 每次请求入口都重新 `load_store`，先只读校验 SQLite 卡片链、`parent_proposal_hash`/version、治理事件链、checkpoint、控制源码和投影漂移，再写恢复状态。后继只按精确父版本和父摘要取代未完成父卡；`COMPLETED_SYNTHETIC` 卡及其 `CLOSED` 成功回执和历史依赖不得被全表更新覆盖。治理库丢失、截断、绑定漂移或恢复结果不确定时 fail closed，禁止自动重注册掩盖历史丢失。
16. SQLite 事务内不写 `active-proposal.json` 或治理投影；事务提交后由请求恢复路径同步投影。正常首次初始化和新需求注册仍允许，已有历史任务缺少治理历史时只阻塞。

# 新项目PM接入检查单

项目：
负责人：
现场核验时间：

## 范围
- [ ] 项目确为新建，未混入历史写者
- [ ] 允许路径、禁止路径、环境和发布边界已记录
- [ ] Git基线、工作树和运行时摘要已保存

## 规则和角色
- [ ] 规则源和append-only事件源已确定
- [ ] 每条硬规则对应失败检查
- [ ] PM / explorer / researcher / worker / architect / verifier职责已分开
- [ ] 模型和备用策略均现场核实；必需审查失败会阻塞

## 审批和执行
- [ ] 审批主体和能力边界已核实
- [ ] task/version/proposal/scope/base/actions/expiry已绑定
- [ ] 普通与破坏性变更权限已分开
- [ ] 未批准、过期、漂移和伪造请求均有失败用例
- [ ] 独立worktree和运行时隔离已验证

## 审查、验收和恢复
- [ ] 大型方案前触发architect
- [ ] 同类错误达到阈值触发architect
- [ ] 长任务结案前触发architect
- [ ] architect与用户批准、独立验收分开
- [ ] 重复请求幂等
- [ ] 未知外部调用保持blocked_unknown
- [ ] 失败证据和successor关系已保留

## 融合权限
- [ ] 默认 L1 无执行；L0 讨论写入拒绝，L1 协作成功
- [ ] observation_scope / control_scope 与 project/environment/task/actor 分别绑定
- [ ] L2 显式负责人操作；L3/通用 adapter 缺失 BLOCKED
- [ ] 旧 epoch、跨 scope/env、discussion 扩权被拒绝
- [ ] pause 与启动/写入事务 fence、重启持久和本地独立停止实测
- [ ] RUNNING/UNKNOWN 保留交接且不重派；旧 permit 未确认结束不声称干净暂停
- [ ] 停止失败先持久 UNKNOWN 再有界释放 fence，pause 明示旧写未静止；每次 begin 缺历史零 permit
- [ ] recover QUEUED 返回 RECOVERY_NOT_STARTED，已有结果恢复不首次启动
- [ ] fixture、真实浏览器、Docker/model 闭环分层记录

## 首个任务
- [ ] 合成演练通过
- [ ] 用户已亲自选择批准/修改/驳回
- [ ] 执行结果与固定产物摘要绑定
- [ ] 独立验收完成
- [ ] 剩余项已转交或按规则结案
- [ ] 生产发布另有授权

结论：PASS / PARTIAL / BLOCKED
证据位置：
剩余项：

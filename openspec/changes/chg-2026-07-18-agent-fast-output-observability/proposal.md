# 为什么

实测普通数据问题的 Wren 命令总耗时约 5 秒，而 Agent 端到端编排约 300 秒，
98% 以上时间消耗在重复发现、读取 56 KB 全量规则、消化多页错误和手工复制 SQL。
与此同时，Graph/普通 Query CLI 会直接输出服务端多行异常，既增加上下文，也暴露
无助于业务用户定位问题的内部堆栈；CLI 又没有结构化阶段计时，无法继续量化优化。

# 变更内容

- 增加 `wren context instructions --compact`：保留正文、列表和小型映射表，仅摘要
  超长 Markdown 审计表；不带参数的完整输出保持不变。
- Graph Query 默认输出紧凑错误，显式 `--verbose-errors` 才输出完整诊断详情；普通
  Query/Dry Plan/Dry Run 复用相同的异常摘要，避免连接器或服务端多页堆栈直出。
- 增加 `wren graph query --timings`，成功和失败均在 stderr 输出单行结构化 JSON，
  stdout 继续只承载 SQL、Plan 或查询结果。
- 将普通问题收敛为项目解析后的 1–3 条命令，并把候选答案 SQL 的生成和执行限制
  为最多两次；第二次只允许基于确定证据修正，信息不足时直接向用户澄清。

# 不做什么

- 不修改 MaxCompute Connector、服务端执行、超时、分区或结果读取逻辑。
- 不保证在业务定义缺失、权限不足或服务不可用时仍能自动得到数据。
- 不为追求成功率增加无限重试、诊断子查询、跨工具绕行或 Agent 自行猜测。
- 不改变 Graph compile-only、Context 完整规则输出及既有结果 stdout 格式。

# 为什么

普通数据问题目前会被 Agent 拆成 context、memory fetch、memory recall、graph
resolve、graph explain、graph query、dry-plan、query、memory store 等多次调用。
其中 Graph Query 只编译 SQL，Agent 还会复制并二次修改 SQL；MaxCompute 连接器
已经具备默认分区策略，这种手工接力既慢又容易让最终执行 SQL 偏离图计划。

# 变更内容

- 将 Wren Usage Skill 的普通数据问题收敛为项目解析后的 1–3 条命令，并规定会话级 Wren
  Memory 熔断、静默降级和显式存储规则。
- 候选答案 SQL 最多生成并执行两次；第二次只允许基于首次错误和现有元数据做唯一、
  确定的修正，信息不足或外部状态错误直接澄清/报告。
- 为 `wren graph query` 增加可选 `--execute` 快路径：在同一进程内完成自然语言
  解析、Graph Plan、SQL 生成、MDL 转换和连接器执行。
- 快路径直接复用现有 `WrenEngine` 与连接器，因此 MaxCompute 默认分区、只读、
  限行、超时和 profile 策略仍由同一执行路径负责。
- 保留不带 `--execute` 的 compile-only 行为，避免破坏已有脚本。

# 不做什么

- 不修改 Semantic Graph Planner、Cube 编译或关系安全规则。
- 不让 Skill 自动存储成功查询；只有用户明确要求保存时才调用 Memory Store。
- 不在 Agent 侧复制或重写连接器生成的默认分区 SQL。

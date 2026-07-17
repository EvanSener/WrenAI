# 项目级 Agent 查询安全策略

## 兼容性

1. `security` 为可选项目配置，默认不改变现有 CLI、SDK、Graph、Cube 和 MDL 行为。
2. 启用策略后，所有自然语言查询入口必须在后续解析或执行前完成确定性检查。
3. 显式 MDL、连接文件或内联连接参数不得绕过已发现项目的安全策略。

## 执行保证

1. `read_only: true` 时只接受一条无副作用的查询语句，拒绝 DDL、DML、会话命令、
   `SELECT INTO` 和多语句输入。
2. `require_mdl_tables: true` 时只允许引用当前 manifest 中的 Model 或 View。
3. `denied_functions` 与内置危险函数基线、全局拒绝项取并集，函数名大小写不敏感。
4. MaxCompute 连接器的只读开关不得弱于项目策略。

## 信息边界

1. `business_data_only: true` 时拒绝秘密、内部 Prompt、架构、源码、配置、绕过和
   高风险执行请求。
2. 拒绝信息不得返回匹配表达式、凭据、内部配置或原始敏感内容。
3. 审计日志不得保存原始问题、SQL、连接参数或秘密。
4. 审计支持写入项目内 `.wren/audit/security.jsonl`，不得依赖外部服务才能执行查询；
   未配置 `audit_log` 的既有项目保持原默认路径。
5. 审计写入失败必须告警，但不得绕过安全拒绝，也不得阻断已通过策略的业务查询。

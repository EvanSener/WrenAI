# 任务

- [x] 定义可插拔 Graph Query Frontend 与自然语言解析结果契约。
- [x] 从 Ontology/Semantic Graph 构造成员目录并支持中英文 label/synonyms。
- [x] 以真实 planner 评估候选锚点并生成稳定 `GraphQueryRequest`。
- [x] 新增 `graph resolve` 与 `graph query/explain --question`。
- [x] 修正 graph explain 对显式 relationshipPath、pathHint、主数据和安全路径的来源标记。
- [x] 增加安全 A→B→C M:1 端到端示例及歧义回归。
- [x] 让 1:M 默认拒绝，仅允许显式 repeat 或已有 Bridge/Allocation。
- [x] 更新市场调研、架构边界和 maxcompute-local 使用说明。
- [x] 执行图功能、旧 Cube/Context 回归和 Ruff 验证。

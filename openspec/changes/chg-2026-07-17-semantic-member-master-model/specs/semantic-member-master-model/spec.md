# ADDED Requirements

### Requirement: 全局成员可声明图查询主模型
系统 SHALL 允许全局 Metric 与 Dimension 通过可选 `master_model` 声明 Graph Query
权威 Binding，且不得将该字段写入旧 Cube runtime MDL。

#### Scenario: Cube 编译隔离
- **WHEN** 全局成员配置了 `master_model` 并被 Cube 引用
- **THEN** Cube 展开结果与未配置该字段时一致，runtime 成员中不出现 `master_model`

### Requirement: 主 Binding 必须可证明
Graph Compiler SHALL 验证主模型存在并暴露成员表达式所需全部原子字段。

#### Scenario: 主模型绑定无效
- **WHEN** `master_model` 不存在或缺少必需字段
- **THEN** `wren graph build` 返回结构化错误且不生成不可信产物

### Requirement: 所有图查询入口统一使用主 Binding
系统 SHALL 在 Queryability、自然语言、结构化 Planner、计算指标和 Explain 中使用
同一主 Binding 规则，显式请求不得静默绕过。

#### Scenario: 非主模型覆盖
- **WHEN** 请求为已配置主模型的成员指定其他 Binding
- **THEN** Planner 返回 `GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN`

### Requirement: 旧维度主数据配置兼容
系统 SHALL 继续读取 `relationships.yml > graph > master_data.attributes`，并在它与
成员 `master_model` 不一致时拒绝构建。

#### Scenario: 新旧声明冲突
- **WHEN** 同一 Dimension 的两个位置声明不同主模型
- **THEN** Graph Compiler 返回 `GRAPH_MASTER_MODEL_CONFLICT`

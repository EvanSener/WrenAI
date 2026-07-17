# 为什么

全局 Metric 和 Dimension 会按表达式所需原子字段自动绑定到所有兼容模型。
当多个模型暴露相同字段时，Graph Query 只能依赖路径、Cube 上下文或调用方显式
锚点裁决；Metric 目前没有权威来源声明，Dimension 的权威来源又集中在
`relationships.yml`，成员定义本身无法完整表达治理边界。

# 变更内容

- 在全局 `metrics/<name>/metadata.yml` 与 `dimensions/<name>/metadata.yml` 增加可选
  `master_model`，只供语义图编译和查询规划使用。
- Graph Compiler 保留全部兼容 Binding 作为血缘证据，但将权威 Binding 标记为
  `isMaster`，并校验主模型存在且包含表达式所需全部原子字段。
- Queryability、自然语言前端、结构化 Planner、计算指标展开和 Explain 统一遵守
  权威 Binding，禁止从非主模型绕过。
- 继续兼容 `relationships.yml > graph > master_data.attributes`；新旧声明冲突时
  fail-closed。

# 不做什么

- 不把 `master_model` 写入旧 Cube/MDL wire format。
- 不改变未配置 `master_model` 的项目、Cube priority 或旧查询行为。
- 不从 `master_model` 创建关系边；图边仍只来自 `relationships.yml > relationships`。

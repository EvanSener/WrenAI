# 为什么

现有语义图已经能把结构化 `GraphQueryRequest` 编译成关系计划和 MaxCompute SQL，
但用户的自然语言问题仍只经过固定 Cube 召回，无法发现 Cube 之外、经
`relationships.yml` 多跳可达的全局 Metric 与 Dimension。因而 A 表指标与 C 表
维度的组合仍需要调用方手写锚点、稳定成员名和路径请求。

# 变更内容

- 新增可插拔 Graph Query Frontend，把自然语言或未来其他图查询输入编译到同一
  `GraphQueryRequest`，不复制 Grain、Fanout、Additivity 和 SQL 方言规则。
- 默认前端从 Ontology Graph 的 Metric/Dimension 节点读取
  `name/label/description/synonyms`，再用 Semantic Graph 的 Binding、Relationship、
  Entity 和 Grain 验证候选。
- 新增 `wren graph resolve`，以及 `wren graph query/explain --question`；保留现有
  `--request` 和 `--source/--metrics/--dimensions` 行为。
- 用户问题先解析成稳定成员和候选锚点，存在等价数据源、角色或路径歧义时返回
  候选并 fail-closed，不让词法匹配绕过图规划安全检查。

# 不做什么

- 不引入 Neo4j/AGE/Gremlin 作为数据执行引擎，业务数据仍在关系仓库。
- 不实现完整 GQL/Cypher；其 pattern syntax 只适合图检查，执行面使用稳定 IR。
- 不修改旧 MDL、Cube 编译和 `wren query` 路径。

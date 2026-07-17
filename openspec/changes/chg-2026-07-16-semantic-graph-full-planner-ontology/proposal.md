# 为什么

第一阶段语义图只能规划单事实安全 M:1 查询，尚不能处理显式 Fanout、多个事实源、Bridge/Allocation、指标可加性以及面向 AI/治理工具的 Ontology 与只读图检查。

# 变更内容

- 在现有 `wren graph` 旁路能力上新增高级关系计划，不修改旧 MDL、Cube 和查询流程。
- 支持从锚点按需遍历任意层级的无环关系路径，将可达节点的维度和指标组合为虚拟宽表计划；两跳索引只作为默认快速发现层。
- 支持多个可达节点的原始字段参与同一行级衍生表达式、叶子字段参与安全的事实聚合，以及多事实合并后的 Post-aggregate 计算。
- 支持已验证 1:M 路径的事实预聚合、去重映射和目标 Grain 汇总。
- 支持多个事实各自聚合后按共同维度/Entity Grain 合并。
- M:N 必须声明可验证的 Bridge 与 Allocation，否则结构化拒绝。
- 支持图级指标 additivity 策略和禁止汇总维度。
- 将模型、指标、维度、同义词和 Cube hierarchy 编译为独立 Ontology Graph。
- 复用现有 OSI/Ossie 转换边界，提供导入导出。
- 提供不执行写操作的受限 GQL/Cypher 风格检查接口。

# 兼容性

- 现有 `plan_virtual_cube`、`wren graph build/show/explain/query` 单事实参数保持可用。
- 新功能只读取新增的可选 `graph.metric_policies`、`graph.bridges` 和结构化请求文件。
- `relationships.yml > relationships` 仍是唯一图边来源；Bridge 配置只能引用已有关系，不能隐式创建边。

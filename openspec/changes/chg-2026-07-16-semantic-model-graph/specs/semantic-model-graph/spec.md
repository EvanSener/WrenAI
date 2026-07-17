## ADDED Requirements

### Requirement: relationships.yml 是图关系唯一事实源
系统 SHALL 只使用项目 `relationships.yml` 中 `relationships` 列表构建模型图的边，不得从 Cube、字段命名或其他文件隐式创建关系边。

#### Scenario: 构建模型图
- **WHEN** 用户执行 `wren graph build`
- **THEN** 产物中的每条关系边都能追溯到 `relationships.yml` 中同名关系

### Requirement: 新功能与旧流程隔离
系统 SHALL 将图能力作为独立命令和产物提供，且不得改变现有 context、Cube 和查询命令的输入、输出与编译行为。

#### Scenario: 旧项目不配置图
- **WHEN** 项目只有既有 `relationships` 列表且继续执行 `wren context build`
- **THEN** 构建结果与引入图功能前保持兼容

### Requirement: 主数据优先解析重复属性
系统 SHALL 支持在 `relationships.yml` 中为全局维度属性指定主模型；当多个节点提供同一属性时，规划器 MUST 优先使用有效的主模型绑定。

#### Scenario: 主模型可达
- **WHEN** 重复维度配置了主模型且主模型可通过安全路径到达
- **THEN** Queryability Index 和虚拟 Cube 计划使用主模型上的维度绑定

#### Scenario: 主模型声明错误
- **WHEN** 主模型不存在或不包含维度表达式依赖的字段
- **THEN** Graph Compiler 以结构化错误拒绝构建

### Requirement: 安全查询能力索引
系统 SHALL 为每个 MetricBinding 计算最多两跳、仅沿安全 `MANY_TO_ONE` 方向可达的维度，并对多条等长路径标记歧义。

#### Scenario: 一对多路径
- **WHEN** 维度只能通过 `ONE_TO_MANY` 或 `MANY_TO_MANY` 路径到达
- **THEN** 第一阶段索引不把该维度标记为可查询

#### Scenario: 基数无法验证
- **WHEN** 关系的一侧没有可用于验证唯一性的主键声明
- **THEN** 关系保留在语义图中并产生诊断，但不得进入安全方向或有效维度索引

### Requirement: 单事实虚拟 Cube
系统 SHALL 能把共享同一事实绑定的指标和安全维度编译为关系计划及目标数据源 SQL，并提供选择路径和拒绝原因。

#### Scenario: 生成 MaxCompute SQL
- **WHEN** 用户选择一个事实模型、一个或多个指标和安全维度
- **THEN** 系统输出只包含所需安全 Join、维度分组和指标聚合的 MaxCompute SQL

## ADDED Requirements

### Requirement: 可达节点形成虚拟宽表
系统 SHALL 支持从锚点遍历任意层级的无环关系路径，并允许路径上或叶子节点的维度与指标进入同一虚拟宽表请求。

#### Scenario: 间接叶子属性
- **WHEN** 请求属性位于锚点三跳之外但存在可验证的无环路径
- **THEN** 高级规划器解析完整路径并将属性加入 Virtual Wide Table Plan，不受两跳快速索引限制

#### Scenario: 多条关系路径
- **WHEN** 同一属性存在多条合法路径且请求未提供 path hint
- **THEN** 规划器返回候选路径和歧义错误，不按最短路静默猜测

#### Scenario: 叶子节点原始属性
- **WHEN** 请求使用可达节点的 `model.field` 属性或由可达字段构成的衍生表达式
- **THEN** 规划器校验字段来源和路径，并将维度表达式加入分组或将聚合表达式加入指标投影

#### Scenario: 多节点行级衍生字段
- **WHEN** 一个维度表达式引用事实节点和一个或多个可达节点字段
- **THEN** 每个输入字段都必须声明且路径可验证，规划器复用公共路径 Join 后再计算表达式

#### Scenario: 叶子字段聚合
- **WHEN** 聚合指标引用可达叶子节点字段
- **THEN** 只有安全 M:1/1:1 路径可以进入当前事实聚合；1:M 或 M:N 必须改用独立事实或有效 Allocation

#### Scenario: 聚合后二次计算
- **WHEN** 表达式引用一个或多个事实已经聚合的输出指标
- **THEN** 系统先完成各事实聚合与共同 Grain 合并，再在输出关系上计算比率或窗口表达式

#### Scenario: 只发现不投影
- **WHEN** 请求设置 `includeReachable` 但没有显式选择成员
- **THEN** 系统返回可达 schema、候选路径和拒绝原因，不生成 Join 或聚合 SQL

### Requirement: Fanout 查询必须预聚合
系统 SHALL 在沿已验证 1:M 方向查询维度时，先按事实主键或声明 Grain 聚合，再通过去重映射连接目标维度。

#### Scenario: 一对多维度查询
- **WHEN** 指标事实节点到目标维度包含 1:M 遍历
- **THEN** 关系计划包含事实预聚合和去重映射步骤，不直接在原始事实行上聚合 Join 结果

### Requirement: 多事实按共同 Grain 合并
系统 SHALL 分别聚合每个事实，并只按所有事实共同可组合的维度或 Entity Grain 合并。

#### Scenario: 两个事实共同按租户汇总
- **WHEN** 两个事实的请求指标都能按规范租户维度计算
- **THEN** 系统生成两个聚合子计划并按租户执行全外连接

### Requirement: M:N 必须显式治理
系统 MUST 拒绝没有有效 Bridge 与 Allocation 的 M:N 遍历。

#### Scenario: 缺少 Allocation
- **WHEN** 查询路径包含 M:N 且没有可验证的 allocation_expression
- **THEN** 规划器返回结构化拒绝，不生成 SQL

### Requirement: Additivity 限制汇总
系统 SHALL 在规划阶段检查指标 additivity 策略及禁止维度。

#### Scenario: 半可加指标按时间汇总
- **WHEN** 指标策略禁止时间维度且请求包含该维度
- **THEN** 规划器拒绝请求并指出指标与受限维度

### Requirement: Ontology Graph 保留中文语义与层级
系统 SHALL 将稳定技术名、label、description、synonyms 和 hierarchy 编译为独立 Ontology Graph。

#### Scenario: Cube hierarchy 编译
- **WHEN** Cube 声明从租户到站点到广告活动的 hierarchy
- **THEN** Ontology Graph 包含有序层级节点和边

### Requirement: OSI/Ossie 互操作不修改 MDL
系统 SHALL 提供 Ontology Graph 与 Open Semantic Interchange 的导入导出，并保持 `target/mdl.json` 不变。

#### Scenario: 导入 OSI
- **WHEN** 用户导入有效 OSI 文件
- **THEN** 系统生成独立 Ontology Graph 和诊断，不写入既有 Model/Cube 文件

### Requirement: 图查询接口只读
系统 SHALL 只执行受限 GQL/Cypher 风格检查语句并拒绝所有写入或过程调用。

#### Scenario: 写语句
- **WHEN** 查询包含 CREATE、MERGE、DELETE、SET、CALL 或 LOAD
- **THEN** 系统在访问图之前拒绝语句

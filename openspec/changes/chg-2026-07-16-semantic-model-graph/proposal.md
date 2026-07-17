## 为什么

固定 Cube 只能从单一 `base_object` 暴露指标和维度，无法安全利用已有模型关系动态组合语义成员。需要在不改变旧流程的前提下，新增由 `relationships.yml` 驱动的语义模型图和单事实虚拟 Cube 能力。

## 变更内容

- 新增独立 `wren graph` 命令组，不接入或改变现有 `context build`、Cube 编译和查询路径。
- 从 `relationships.yml` 构建有向模型图、Entity、Grain、MetricBinding 和 DimensionBinding。
- 在 `relationships.yml` 的可选 `graph.master_data.attributes` 中指定重复维度属性的主模型。
- 预计算最多两跳安全 `MANY_TO_ONE` 路径下的 `metric -> valid dimensions`。
- 新增路径解释和单事实、多维度 MaxCompute SQL 生成。
- 暂不开放一对多、多事实、多对多和本体推理执行。

## 能力范围

### 新增能力

- `semantic-model-graph`: 关系图构建、主数据裁决、查询能力索引、路径解释和单事实虚拟 Cube SQL。

### 修改能力

- None。

## 影响范围

- `core/wren/src/wren/` 新增图编译器、规划器和 CLI 模块。
- `core/wren/src/wren/cli.py` 仅注册新的命令组。
- Wren 项目的 `relationships.yml` 可选增加 `graph` 配置；既有 `relationships` 数据结构保持不变。


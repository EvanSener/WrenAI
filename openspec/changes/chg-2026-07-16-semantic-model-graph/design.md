## 背景

当前全局 Metric 和 Dimension 在构建 Cube 时仍被展开并校验为单一 `base_object` 的本地字段。`relationships.yml` 已保存模型关系，但只进入旧 MDL，没有独立的查询能力图、路径歧义检查或主数据裁决。

## 目标 / 非目标

**目标：**

- 新增一个完全旁路的 Python Graph Compiler 和 CLI。
- 关系边严格来自 `relationships.yml`。
- 对重复全局维度绑定使用显式主模型。
- 支持两跳安全 M:1 查询能力和单事实 SQL。

**非目标：**

- 不改旧 MDL、Cube Schema、Rust Planner 或现有命令行为。
- 本阶段不执行一对多、多事实、多对多、Allocation、Ontology 或 GQL。

## 设计决策

1. `relationships.yml` 顶层允许可选 `graph.master_data.attributes`；旧 loader 继续只读取 `relationships`。
2. Graph Compiler 读取现有 Model、View、Metric、Dimension 元数据，输出独立 `target/semantic_graph.json` 和 `target/queryability_index.json`。
3. 关系顺序与 `join_type` 共同确定声明方向：M:1 使用第一个模型指向第二个模型，1:M 反向；只有基数通过主键校验的关系才进入安全方向，无法验证的关系保留在图中但 fail-closed，M:N 不进入第一阶段安全图。
4. MetricBinding 和 DimensionBinding 由 SQL AST 原子字段与模型字段交集推导，不新增重复指标定义。
5. 主数据按全局维度稳定名配置；若主模型绑定有效则覆盖本地或其他模型绑定。
6. Dynamic Virtual Cube 生成中间关系计划 JSON，再由同一模块渲染目标方言 SQL。

## 风险 / 权衡

- `[关系基数声明不真实]` → 静态检查主键和方向，输出未验证警告；后续增加数据探测。
- `[密集图存在多条路径]` → 只采用唯一最短安全路径，等长路径报歧义。
- `[主数据绕远导致额外 Join]` → 主数据是显式治理选择，路径仍受两跳上限约束。
- `[当前阶段只有单事实]` → 结构化拒绝，不退化为不安全大 Join。

## 迁移方案

无需迁移。旧项目不执行 `wren graph` 时没有行为变化；删除新增产物和可选 `graph` 配置即可回滚。

## 开放问题

- 后续 Fanout Planner 是否直接进入 Rust/DataFusion，待第一阶段产物和 SQL 计划稳定后决定。

# Wren 语义图与虚拟宽表方案

## 结论

可以基于 Wren 实现，但不能把“图查询”理解成把所有可达表直接 Join。
正确做法是把关系图作为查询规划空间：从锚点发现任意层级可达成员，先按
Entity/Grain 判定可组合性，再为每个事实独立聚合，最后生成按需的 Virtual
Wide Table Plan 和 MaxCompute SQL。

第一阶段的两跳 Queryability Index 继续保留，作用是快速、低风险地召回
常用维度；高级查询按需遍历无环 simple path，默认最大深度为节点数减一，
因此可以访问间接叶子节点。存在多条路径时必须显式指定 path hint，不能仅凭
最短路猜测业务语义。

## 市面方案的可借鉴点

### Ontop Virtual Knowledge Graph

Ontop 不复制关系库明细，而是用映射把关系表暴露成虚拟知识图谱。SPARQL 查询
先解析成图模式，再经过 mapping unfolding 下推为 SQL。这与 Wren 的目标最接近：
图负责语义发现与路径规划，MaxCompute 仍负责数据计算。

Wren 应借鉴：保留图查询前端、稳定逻辑 IR、关系映射和 SQL 执行器四层边界；
不照搬 RDF/OWL 作为 Wren 唯一建模语言，也不把 Ontology 当成 Join 事实源。

### SQL/PGQ

ISO SQL/PGQ 在 SQL 中定义 Property Graph，并用图模式匹配产生可继续参与 SQL
计算的关系结果。DuckPGQ 已证明这条路线可以落在分析型关系引擎上。

Wren 应借鉴：图模式与关系代数之间应有明确 lowering 阶段；不应把 Cypher/GQL
字符串直接替换成 SQL。当前 `GraphQueryRequest` 就是该 lowering 的稳定边界，
以后可以增加 SQL/PGQ 或 GQL 子集前端，而无需修改 Grain/Fanout Planner。

### dbt MetricFlow

MetricFlow 官方把 Semantic Model 作为节点、Entity 作为边，根据 Entity 类型
动态生成 Join，并明确以避免 fanout/chasm join 为目标。指标先由带聚合类型的
Measure 构成，维度可以位于其他 Semantic Model；多事实查询采用各事实先聚合
再按共同 Entity/Grain 合并的路线。

Wren 应借鉴：Entity 类型、合法维度发现、每事实独立聚合、共同 Grain 合并，
以及“不是所有图路径都可查询”的 fail-closed 原则。

### Looker

Looker 使用 symmetric aggregates 修正 Join fanout，但官方兼容表显示 Hive
2.3/3.1 不支持该能力。Wren 的 MaxCompute SQL 当前经 SQLGlot Hive 方言生成，
因此不能把 symmetric aggregate 当作主要正确性机制。

Wren 应借鉴：主键/关系基数是聚合正确性的必要元数据；不照搬依赖复杂
DISTINCT 哈希表达式的修正方案，而采用预聚合和去重映射。

### Malloy

Malloy 把可复用 dimension/measure 和 join 关系放进 Semantic Model，查询只选
所需字段并生成 SQL。它证明了“模型定义图 + 查询时组合”比预先生产所有宽表
更适合作为交互层。

Wren 应借鉴：按需投影、可复用计算和嵌套结果；不把整张图预先物化。

### Cube

Cube 的 Join 模型要求显式 relationship 和 primary key，并在行倍增时围绕
事实主键做去重聚合；View 可把多个 Cube 的成员整理成面向查询的语义数据集，
pre-aggregation/aggregate awareness 则按查询形状匹配物化 rollup。

Wren 应借鉴：稳定 member namespace、PK/cardinality 作为 Fanout gate、按事实
独立 rollup、查询时二次计算，以及后续按热度增加可选缓存；不能照搬 Cube
Store 或静态 Cube Join 拓扑，更不能让 pre-aggregation 成为正确性的前提。

### Lightdash

Lightdash 把计算区分为行级 Dimension、Aggregate Metric、Non-aggregate Metric
和 Post-calculation，并支持 label、description、format、底层明细字段以及同表
多角色 Join alias。

Wren 应借鉴：稳定技术名与展示语义分离，并把计算拆成“Join 前行表达式 →
事实聚合 → 多事实共同 Grain 合并 → 聚合后二次表达式”。Lightdash 的 dbt
Model Join 仍是人工局部图，不能替代 Wren 的全局 Entity/关系规划。

### Apache Calcite 关系代数、Lattice 与物化视图重写

Calcite 先把查询降为关系算子，再通过规则改写；Lattice 则把星型关系定义为
不物化的 big virtual join view，让优化器把查询匹配
到 Filter/Join/Project/Aggregate 物化视图。但官方明确 Lattice 只覆盖中心事实向
外 M:1 的星型/雪花模型，并且不会替用户验证声明约束。

Wren 应借鉴：图是逻辑规划空间、虚拟宽表不是物理宽表、可选 tile/rollup 只做
加速；不应直接嵌入 Java Calcite 替换现有 Python/SQLGlot 规划器，也不能用
Lattice 表示角色、多事实、1:M 和 M:N 通用图。

### 多事实规划

成熟语义层普遍支持独立事实节点和共享维度，但多路径图不能只靠最短路：相同
节点之间可能存在下单人、付款人、收货人等角色关系。多事实的可靠实现主要有
两类：各事实预聚合后 FULL OUTER JOIN，或每事实生成 UNION ALL leg 后再汇总。
Wren 首选前者以保持列式指标输出；无法形成共同 Entity/Grain 时拒绝，不生成
原始事实间大 Join。

### GQL/openCypher、Apache Ossie 与知识图谱

ISO/IEC 39075:2024 GQL 和 openCypher 为 Property Graph 提供成熟的 pattern
matching 语法；Apache Ossie（原 Open Semantic Interchange）提供厂商中立的
JSON/YAML 语义模型交换。

Wren 只借鉴 `MATCH/WHERE/RETURN/ORDER BY/LIMIT` 做元数据检查和路径 explain；
GQL/Cypher 本身不知道 Metric Grain、Fanout、Additivity，也不负责生成正确的
MaxCompute 聚合 SQL，所以不能充当 Virtual Wide Table Planner。Ossie 同样只
负责交换，不负责选路和执行。

Neo4j/Cypher、Apache AGE 和 TinkerPop/Gremlin 适合直接在图引擎中执行图数据，
但不会为 Wren 生成可移植的 MaxCompute SQL。把仓库明细复制进图数据库还会引入
双份数据、权限和一致性问题，因此不作为主执行路线。图数据库可以作为只读元数据
检查或外部知识图谱适配器，但不能绕过 Wren Planner。

MetricFlow 官方已支持 Open Semantic Interchange（OSI/Ossie）；Dremio 等方案
也把知识图谱放在物理执行层之上。Wren 因此应保持两个边界：

- `semantic_graph.json`：关系、Grain、Binding 和 SQL 规划事实源。
- `ontology_graph.json`：label、description、synonyms、hierarchy 和互操作。

Ontology 可以帮助 AI 发现成员，但不得替代 `relationships.yml` 的物理 Join。

## “所有可达属性参与计算”的准确语义

可以保证的是：从锚点发现所有直接或间接、路径上或叶子节点的候选成员，并
允许用户申请把以下成员放入一次查询：

- 节点原始 `model.field`；
- 独立治理的 Dimension、Time Dimension 和 Metric；
- 由一个或多个可达字段构成的行级 `CASE` 等衍生维度；
- 在单一事实 Grain 上计算的聚合指标；
- 多事实各自聚合并合并后的比率等 Post-aggregate 计算。

不能保证“任意组合必然执行”。同名属性没有主数据、路径有歧义、跨独立 1:M
分支、M:N 没有 Bridge/Allocation、多个事实没有共同 Grain、指标在目标维度上
不可加时必须返回结构化拒绝。否则所谓“像宽表”只是更方便地产生错误数字。

计算顺序固定为：

```text
可达字段与行级表达式
        ↓
每个事实按自身 Grain 聚合
        ↓
按共同 Entity/Grain 合并多个事实
        ↓
Post-aggregate 比率/窗口等二次计算
```

## Wren 落地架构

```text
自然语言 / BI / 未来 SQL/PGQ 或 GQL 子集
                         │
                         ▼
              GraphQueryFrontend
                         │
                         ▼
Ontology 成员召回（Metric / Dimension / label / synonyms）
                         │
                         ▼
relationships.yml + models/views + metrics/dimensions/cubes
                         │
                         ▼
              Semantic Graph Compiler
                         │
       ┌─────────────────┴─────────────────┐
       ▼                                   ▼
semantic_graph.json                ontology_graph.json
       │                                   │
       ▼                                   ▼
Binding / Reachability / Path Resolver  GQL/Cypher Inspector
       │
       ▼
GraphQueryRequest → Grain-safe Planner
  ├─ M:1 / 1:1: 直接安全 Join
  ├─ 1:M: 事实预聚合 + DISTINCT 映射
  ├─ M:N: Bridge + Allocation，否则拒绝
  └─ Multi-fact: 每事实聚合 + 共同 Grain 合并
       │
       ▼
Virtual Wide Table Plan → MaxCompute SQL
```

Virtual Wide Table 的 schema 可以包含：

- 全局 Metric 和 Dimension；
- 任意可达节点的 `model.field` 属性；
- 只引用可达字段、经 SQL AST 校验的衍生维度或聚合计算；
- 每个成员的来源节点、路径、Entity、Grain、additivity 和拒绝原因。

`includeReachable` 可以发现整张可达子图，但 SQL 只 Join 和投影请求真正使用的
成员，避免“为了像宽表”而每次扫描整张图。

## 本次 Wren/MaxCompute 落地结果

真实 `maxcompute-local` 已编译出 51 个节点、142 条边、102 个 Metric Binding、
131 个 Dimension Binding，以及 4,329 个 Ontology 节点和 4,756 条 Ontology
边。`wren graph discover` 从推广品事实锚点列出 3,115 个原始 `model.field`
候选和 10 个可达全局指标；没有 path hint 时，7 个存在多路径歧义的全局维度
只进入 rejected 列表，不会被静默投影。

当前三个真实请求分别验证：

- 单事实安全计划：规范维度、主数据字段、跨事实/活动节点行级 `CASE`、
  `MAX(叶子字段)` 和事实聚合指标共用 Join 路径；
- 两跳 1:M 叶子计划：推广品事实经租户到用户，只有显式声明
  `fanoutMode: repeat` 后才生成事实预聚合、原始映射、去重映射和目标 Grain
  回卷四个阶段；该结果不可跨用户状态加总，只验证 SQL 形状；
- 双事实计划：推广品和搜索词分别聚合后按租户/站点 `FULL OUTER JOIN`，再计算
  combined cost 和 impression share 等 Post-aggregate 指标。

自然语言入口已经新增一条不依赖 Cube 固定维度列表的路径：

```text
按站点看曝光量
→ impressions_sum + marketplace
→ MetricBinding 候选事实源
→ Cube priority 仅作为同分查询上下文
→ relationships.yml 唯一安全路径
→ GraphQueryRequest
→ MaxCompute SQL
```

相同证据的数据源、等长路径和角色关系保持歧义；未知维度不会被静默丢弃。
自然语言入口只自动选择 M:1/1:1，发现 1:M 时要求调用方改用显式结构化请求。

`includeReachable` 已与 `metrics: "*"` / `dimensions: "*"` 分离：前者只返回候选
schema，后两者才表示显式申请投影。无治理 M:N 后方的字段仍会以
`declared_only` 候选出现，但查询继续要求 Bridge/Allocation。这样“都能发现”
和“只有安全组合才能执行”可以同时成立。

## 安全边界

1. 图边只来自 `relationships.yml > relationships`。
2. 任意深度指无环 simple path，不允许在环里无限遍历。
3. 多路径必须 path hint；角色不同的关系不能自动合并。
4. 无主键/Grain 的 Fanout 路径 fail-closed；普通 1:M 默认也拒绝，只有
   Allocation 或显式 `fanoutMode: repeat` 才能继续。
5. M:N 必须有现存 Bridge Model、两条现存关系和 Allocation 表达式。
6. 非可加或半可加指标遵守 blocked dimensions。
7. 图查询 DSL 只读，拒绝 CREATE/MERGE/DELETE/SET/CALL/LOAD。

## 图查询语言选择

执行面不自研一套完整 GQL。当前把 YAML/JSON `GraphQueryRequest` 作为稳定 IR：
它能直接表达 facts、metrics、dimensions、attributes、calculations、inputs、
entity grain、maxDepth 和 pathHints，随后编译成 Relational Plan 与 MaxCompute
SQL。未来无论接自然语言、BI 拖拽还是 GQL 风格语法，都只需翻译到这份 IR，
不用复制正确性规则。

当前实现以可插拔 `GraphQueryFrontend` 协议落实开闭原则：

- `NaturalLanguageGraphFrontend`：Ontology 中文/英文词汇召回；
- 结构化 YAML/JSON：直接提供稳定 `GraphQueryRequest`；
- 未来 BI、SQL/PGQ 或 GQL 子集：只新增 Adapter，输出同一 IR；
- Planner、Relational Plan 和 MaxCompute Renderer 不感知输入语言。

GQL/Cypher 风格接口只用于 `semantic_graph.json` / `ontology_graph.json` 的只读
检查和 explain。该分工利用成熟 pattern matching 的可读性，同时把 Grain、
Fanout、Allocation、Additivity 和 SQL 方言继续留在关系规划器中。若未来确有
外部兼容需求，可基于 openCypher grammar/TCK 扩展 parser；当前没有必要自研
完整语法、事务、写操作和图数据库运行时。

## A → B → C 如何编译成 SQL

用户只问“按 C 地区看 A 销售额”。Ontology 先识别 `revenue` 和
`leaf_region`；MetricBinding 把 A 定为事实锚点；Path Resolver 从
`relationships.yml` 选择 `a_b → b_c`。生成的 IR 类似：

```yaml
schemaVersion: 1
facts:
  - sourceModel: fact_a
    metrics: [revenue]
dimensions:
  - name: leaf_region
    bindingModel: leaf_c
    relationshipPath: [a_b, b_c]
```

关系计划最终仍会生成两层 Join。图查询的价值是自动发现、解释和校验路径，
不是让关系数据库跳过 Join：

```sql
SELECT
  c.region AS leaf_region,
  SUM(a.amount) AS revenue
FROM fact_a AS a
LEFT JOIN bridge_b AS b ON a.b_id = b.id
LEFT JOIN leaf_c AS c ON b.c_id = c.id
GROUP BY c.region;
```

若 A → B 或 B → C 方向为 1:M、存在 billing/shipping 两条等价角色路径，或 C
不是配置的主数据绑定，Planner 会先拒绝或要求显式选择，不会先生成 SQL 再碰运气。

### Metric/Dimension 的权威来源

全局 `metrics/<name>/metadata.yml` 和 `dimensions/<name>/metadata.yml` 可选声明：

```yaml
master_model: dim_marketplace
```

它只裁决 Graph Query 的多 Binding 冲突，不创建边，也不会进入旧 Cube/MDL。
Graph Compiler 仍保留全部兼容 Binding 作为血缘，但只把权威 Binding 标记为
`isMaster: true`。权威模型必须是图节点，而且必须包含表达式展开后的全部原子
字段；不存在或字段不全时构建失败。自然语言、结构化 Planner、计算指标和 Explain
共用同一规则，显式指定非权威模型通常返回
`GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN`。Dimension 有一个受控例外：若事实本地
Binding 的全部原子字段被安全关系的 `conditionColumns` 证明为同一主数据关系键，
Planner 可直接投影该本地键并在 Explain 标记 `sourceEquivalentMasterKey`；描述性
属性仍必须使用权威 Binding。

旧的 `relationships.yml > graph > master_data.attributes` 继续作为 Dimension
兼容入口；新旧位置同时配置且值不同会返回 `GRAPH_MASTER_MODEL_CONFLICT`。
未配置 `master_model` 的成员继续按路径、角色和显式锚点处理，行为不变。

## GraphQueryRequest 核心字段

| 字段 | 作用 |
| --- | --- |
| `schemaVersion` | GraphQueryRequest 契约版本；当前只接受 `1`，未知版本和未知顶层字段直接拒绝。 |
| `anchorModel` | 发现成员和自动选择 Metric Binding 的锚点；不等于必须从该表执行所有指标。 |
| `includeReachable` | 返回可达 raw field、Metric、Dimension 及拒绝原因；不隐式投影。 |
| `maxDepth` | 无环路径的最大关系步数；省略时上限为节点数减一，搜索仍受 expansion cap 保护。 |
| `facts` | 显式事实块，每块声明 `sourceModel` 和该事实负责计算的 `metrics`。 |
| `metrics` | `anchorModel` 自动模式下的全局指标；可用 `"*"` 显式申请全部可安全绑定指标。 |
| `dimensions` | 所有事实共同输出的全局维度；可指定 `bindingModel`、`relationshipPath`、`role`。 |
| `attributes` | 原始 `model.field` 投影，必须指定唯一输出 `alias`，可带路径/角色。 |
| `calculations` | `dimension` 为行级维度；`metric` 为事实聚合；`post_metric` 为聚合/合并后的二次指标。 |
| `calculations[].inputs` | 跨节点行级或聚合计算使用的精确 `model.field` 白名单，每个输入可独立指定路径。 |
| `entityGrain` | 显式声明多事实合并使用的共同 Entity key；各事实必须暴露兼容字段。 |
| `fanoutMode` | 默认 `reject`；`repeat` 表示接受同一事实重复归属多个子维度，结果不可跨这些成员加总。 |
| `pathHints` | 为 Dimension、Metric、Attribute 或 Calculation 消除多路径和角色歧义。 |

`facts` 与顶层 `metrics` 自动模式二选一。发现到成员只说明图上有关联；只有在
路径、Grain、基数和 Additivity 都通过后，它才进入 `RelationalPlan` 和 SQL。

## 本轮实时核验资料

- Ontop Virtual Knowledge Graph：
  <https://ontop-vkg.org/guide/>
- ISO SQL/PGQ 与 DuckPGQ：
  <https://www.iso.org/standard/79473.html>
  <https://duckdb.org/community_extensions/extensions/duckpgq>
- Apache Calcite 关系代数：
  <https://calcite.apache.org/docs/algebra.html>

- dbt MetricFlow 概念与 Semantic Graph：
  <https://docs.getdbt.com/docs/build/about-metricflow>
- dbt MetricFlow Join 逻辑：
  <https://docs.getdbt.com/docs/build/join-logic>
- dbt Semantic Layer 工作原理：
  <https://www.getdbt.com/blog/how-the-dbt-semantic-layer-works>
- Looker symmetric aggregates 与方言支持：
  <https://cloud.google.com/looker/docs/reference/param-explore-symmetric-aggregates>
- Malloy Semantic Model 查询：
  <https://docs.malloydata.dev/documentation/user_guides/querying_a_model>
- Malloy Source / Query / View：
  <https://docs.malloydata.dev/documentation/language/source>
  <https://docs.malloydata.dev/documentation/language/query>
  <https://docs.malloydata.dev/documentation/language/views>
- Cube Join、数据融合与 Pre-aggregation：
  <https://cube.dev/docs/product/data-modeling/reference/joins>
  <https://cube.dev/docs/product/data-modeling/concepts/data-blending>
  <https://cube.dev/docs/product/caching/using-pre-aggregations>
- Lightdash Dimension、Metric 与 Join：
  <https://docs.lightdash.com/references/dimensions>
  <https://docs.lightdash.com/references/metrics>
  <https://docs.lightdash.com/references/joins>
- Apache Calcite Lattice 与物化视图重写：
  <https://calcite.apache.org/docs/lattice.html>
  <https://calcite.apache.org/docs/materialized_views.html>
- ISO GQL、openCypher 与 MATCH：
  <https://www.iso.org/standard/76120.html>
  <https://opencypher.org/resources/>
  <https://neo4j.com/docs/cypher-manual/current/clauses/match/>
- Apache Ossie：
  <https://github.com/open-semantic-interchange/OSI>

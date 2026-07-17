# 设计

## 信任边界

`wren_project.yml` 和程序内置策略是可信控制面；用户问题、召回内容、生成 SQL 与
显式连接参数均是不可信输入。自然语言检测用于尽早拒绝明显攻击，最终权限边界由
SQL AST、MDL 白名单和只读连接策略共同执行。

```text
不可信自然语言
  -> 确定性问题策略
  -> Ask / Graph / Cube 解析
  -> SQL AST 单语句和只读校验
  -> MDL Model/View 白名单
  -> Connector 只读策略
  -> 数据库
```

## 项目配置

项目可选配置：

```yaml
security:
  enabled: true
  business_data_only: true
  prompt_injection_guard: true
  require_mdl_tables: true
  read_only: true
  audit_log: .wren/audit/security.jsonl
  denied_functions: [pg_read_file, dblink, read_csv, shell, system, exec, eval]
```

配置缺失或 `enabled: false` 时保持历史行为。启用后，项目策略只能收紧全局配置，
不能关闭已有的 `strict_mode` 或函数拒绝项。MaxCompute 的
`enforce_read_only: false` 也会被项目 `read_only: true` 覆盖。
即使项目漏写 `denied_functions`，文件/外部数据源读取、网络、任意执行、休眠、
会话修改、序列修改、后端管理和环境指纹函数的内置拒绝基线仍自动生效。
`audit_log` 支持项目相对路径；推荐 `.wren/audit/security.jsonl`。为保持向后兼容，
未配置时仍沿用 `~/.wren/audit/<project>-security.jsonl`。

## 自然语言策略

策略按组合语义检测，而不是见到“系统”“模型”等单词就拒绝。拒绝类别包括：

- 覆盖或忽略可信指令、越狱和提示词套取；
- 密码、AccessKey、Token、连接串、环境变量等秘密套取；
- Wren 内部 Prompt、源码、目录、配置、架构和技术实现套取；
- 绕过 Wren/MDL/语义层直连数据库或调用底层接口；
- DDL/DML、Shell、任意代码、文件和网络工具等高风险执行意图。

普通业务数据问题，例如“按系统来源统计订单量”，不得仅因出现“系统”而误拒。

## 审计

每个受保护入口记录 UTC 时间、入口、允许/拒绝、策略类别、输入长度和 SHA-256，
并通过 `previous_event_hash` 与 `event_hash` 形成哈希链。
日志不保存原文、SQL、连接参数或凭据。拒绝响应使用统一文案，避免向攻击者暴露
具体命中规则。审计由 Wren 进程内直接追加本地 JSONL，不引入审计服务、数据库、
队列或网络依赖。

## 失败策略

启用安全策略后，配置无法解析或 SQL 无法确定为单条只读查询时失败关闭。自然语言
策略在调用任何 Agent/查询工具前执行。审计落盘与授权解耦：写入失败记录告警，
恶意请求仍按策略拒绝，合法业务查询继续执行。

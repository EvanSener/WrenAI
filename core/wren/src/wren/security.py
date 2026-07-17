"""Deterministic project security controls for Wren agent query entrypoints.

The LLM is not part of this enforcement path.  Project policy is loaded from
``wren_project.yml``; obvious malicious natural-language requests are rejected
before semantic resolution, and the same policy is later merged into SQL AST
validation by the CLI engine factory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from wren.config import WrenConfig
from wren.model.error import ErrorCode, ErrorPhase, WrenError

_PROJECT_FILE = "wren_project.yml"
_GENESIS_HASH = "0" * 64
_LOGGER = logging.getLogger(__name__)
_REJECTION_MESSAGE = "该请求不属于允许的业务数据查询范围，已被 Wren 安全策略拒绝。"
_SECURITY_FIELDS = {
    "enabled",
    "business_data_only",
    "prompt_injection_guard",
    "require_mdl_tables",
    "read_only",
    "audit_log",
    "denied_functions",
}
_BASELINE_DENIED_FUNCTIONS = frozenset(
    {
        # Files, external storage and cross-database readers.
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "load_file",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_parquet",
        "sqlite_scan",
        "postgres_scan",
        "mysql_scan",
        "dblink",
        "dblink_exec",
        # Network, arbitrary execution and denial-of-service helpers.
        "http_get",
        "http_post",
        "shell",
        "system",
        "exec",
        "eval",
        "pg_sleep",
        "sleep",
        "benchmark",
        # Session mutation, sequence mutation and backend administration.
        "set_config",
        "setval",
        "nextval",
        "pg_advisory_lock",
        "pg_try_advisory_lock",
        "pg_cancel_backend",
        "pg_terminate_backend",
        "pg_reload_conf",
        # Internal environment and implementation fingerprinting.
        "current_setting",
        "version",
        "current_database",
        "current_user",
        "session_user",
        "inet_server_addr",
        "inet_server_port",
        "pg_backend_pid",
    }
)


@dataclass(frozen=True)
class ProjectSecurityPolicy:
    """Trusted project policy loaded from ``wren_project.yml``."""

    enabled: bool = False
    business_data_only: bool = True
    prompt_injection_guard: bool = True
    require_mdl_tables: bool = True
    read_only: bool = True
    audit_log: Path | None = None
    denied_functions: frozenset[str] = field(default_factory=frozenset)


_INJECTION_PATTERNS = (
    re.compile(
        r"(?:忽略|无视|覆盖|绕过|取消|删除).{0,16}"
        r"(?:之前|以上|系统|开发者|安全|规则|指令|限制|提示词|prompt)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:ignore|disregard|override|bypass|forget).{0,48}"
        r"(?:previous|prior|system|developer|security|policy|instruction|prompt|rules?)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:越狱|开发者模式|jailbreak|developer\s+mode|dan\s+mode|"
        r"do\s+anything\s+now)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:输出|显示|泄露|告诉|打印|复述|reveal|show|print|leak|repeat).{0,28}"
        r"(?:系统提示词|开发者指令|隐藏指令|内部提示词|system\s+prompt|"
        r"developer\s+message|hidden\s+instructions?)",
        re.IGNORECASE | re.DOTALL,
    ),
)

_SECRET_PATTERNS = (
    re.compile(
        r"(?:给我|显示|输出|打印|泄露|告诉|读取|获取|查询|查看|查找|导出|列出|"
        r"show|reveal|print|dump|read|get|export|exfiltrate|list).{0,36}"
        r"(?:密码|密钥|令牌|凭据|连接串|连接信息|连接配置|环境变量|"
        r"profiles?\.yml|\.env|access[_\s-]?key|"
        r"secret|password|passwd|token|credentials?|connection\s+string|"
        r"environment\s+variables?)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:access[_\s-]?key[_\s-]?secret|access[_\s-]?id|"
        r"aws_secret_access_key|odps_access_id|odps_access_key).{0,20}"
        r"(?:是多少|是什么|给我|输出|显示|reveal|show|print|dump|value)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:access[_\s-]?key[_\s-]?secret|odps[_\s-]?access[_\s-]?key|"
        r"数据库密码|连接密码|账号密码|密钥明文|令牌值|token\s+value|credentials?)",
        re.IGNORECASE,
    ),
)

_INTERNAL_PATTERNS = (
    re.compile(
        r"(?:系统提示词|内部提示词|系统架构|内部架构|架构体系|技术栈|"
        r"技术选型|技术细节|源代码|源码|目录结构|配置文件|部署拓扑|内部实现|"
        r"system\s+prompt|internal\s+architecture|tech\s+stack|source\s+code|"
        r"internal\s+implementation)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:wren(?:ai)?|系统内部|内部系统).{0,40}"
        r"(?:架构|技术栈|技术选型|技术细节|源码|源代码|实现|目录|路径|配置|依赖|版本|"
        r"提示词|prompt|architecture|tech\s+stack|source\s+code|"
        r"implementation|directories?|paths?|configs?|dependencies|version)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:架构|技术栈|源码|源代码|实现细节|目录结构|配置文件|"
        r"architecture|tech\s+stack|source\s+code|implementation|config).{0,32}"
        r"(?:wren(?:ai)?|系统内部|internal\s+system)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:介绍|说明|查询|查看|列出|输出|告诉|describe|explain|show|list|reveal).{0,20}"
        r"(?:系统架构|架构体系|技术栈|技术选型|技术细节|源码|源代码|"
        r"内部实现|目录结构|配置文件|部署拓扑|组件版本|system\s+architecture|"
        r"tech\s+stack|source\s+code|internal\s+implementation)",
        re.IGNORECASE | re.DOTALL,
    ),
)

_BYPASS_PATTERNS = (
    re.compile(
        r"(?:绕过|跳过|避开|不经过|直接调用|直连|直接连接|bypass|skip|"
        r"circumvent|direct(?:ly)?\s+(?:call|connect|query|access)).{0,52}"
        r"(?:wren(?:ai)?|mdl|model|模型|语义层|数据库|database|connector|"
        r"odps|maxcompute)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:数据库|database).{0,24}(?:底层接口|原生接口|raw\s+api|"
        r"connector\s+api).{0,24}(?:执行|查询|execute|query|run)",
        re.IGNORECASE | re.DOTALL,
    ),
)

_HIGH_RISK_PATTERNS = (
    re.compile(
        r"\b(?:drop|truncate|alter|grant|revoke)\s+"
        r"(?:table|database|schema|user|role)\b|\bdelete\s+from\b|"
        r"\binsert\s+into\b|\bupdate\s+[^;]{0,80}\s+set\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:执行|运行|execute|run).{0,40}"
        r"(?:shell|bash|powershell|os\.system|subprocess|rm\s+-rf|curl\s+|"
        r"wget\s+|任意代码|恶意代码)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:base64|十六进制|hex|rot13).{0,24}(?:解码|decode).{0,24}"
        r"(?:执行|运行|execute|run)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:读取|写入|下载|上传|删除|read|write|download|upload).{0,24}"
        r"(?:/etc/passwd|文件系统|本地文件|file\s+system|s3://|oss://)",
        re.IGNORECASE | re.DOTALL,
    ),
)

_BUSINESS_SIGNAL = re.compile(
    r"(?:统计|查询|分析|汇总|分组|趋势|同比|环比|占比|转化率|多少|哪些|"
    r"排名|明细|下钻|曝光|点击|花费|销售|订单|收入|成本|利润|租户|站点|"
    r"广告|活动|商品|推广品|日期|每天|每周|每月|表现|情况|最近)|"
    r"\b(?:count|sum|average|avg|trend|compare|breakdown|group\s+by|top|"
    r"bottom|revenue|sales|orders?|spend|cost|clicks?|impressions?|"
    r"campaigns?|customers?|tenants?|sites?|metrics?|dimensions?|data)\b",
    re.IGNORECASE,
)


def load_project_security(project_path: Path | None) -> ProjectSecurityPolicy:
    """Load and validate a project's optional security block."""

    if project_path is None:
        return ProjectSecurityPolicy()
    config_path = project_path / _PROJECT_FILE
    if not config_path.exists():
        return ProjectSecurityPolicy()
    try:
        project = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _configuration_error(
            "cannot read project security configuration"
        ) from exc
    if not isinstance(project, dict):
        raise _configuration_error("project configuration must be an object")

    raw = project.get("security")
    if raw is None:
        return ProjectSecurityPolicy()
    if not isinstance(raw, dict):
        raise _configuration_error("'security' must be an object")
    unknown = sorted(set(raw) - _SECURITY_FIELDS)
    if unknown:
        raise _configuration_error("unknown security field(s): " + ", ".join(unknown))

    enabled = _read_bool(raw, "enabled", False)
    business_data_only = _read_bool(raw, "business_data_only", True)
    prompt_injection_guard = _read_bool(raw, "prompt_injection_guard", True)
    require_mdl_tables = _read_bool(raw, "require_mdl_tables", True)
    read_only = _read_bool(raw, "read_only", True)

    denied_raw = raw.get("denied_functions", [])
    if not isinstance(denied_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in denied_raw
    ):
        raise _configuration_error(
            "'security.denied_functions' must be an array of non-empty strings"
        )
    denied_functions = frozenset(item.strip().casefold() for item in denied_raw)

    audit_raw = raw.get("audit_log")
    if audit_raw is not None and (
        not isinstance(audit_raw, str) or not audit_raw.strip()
    ):
        raise _configuration_error(
            "'security.audit_log' must be a non-empty path string"
        )
    if audit_raw is None:
        project_name = _safe_project_name(project.get("name"))
        audit_path = Path.home() / ".wren" / "audit" / f"{project_name}-security.jsonl"
    else:
        audit_path = Path(audit_raw).expanduser()
        if not audit_path.is_absolute():
            audit_path = project_path / audit_path

    return ProjectSecurityPolicy(
        enabled=enabled,
        business_data_only=business_data_only,
        prompt_injection_guard=prompt_injection_guard,
        require_mdl_tables=require_mdl_tables,
        read_only=read_only,
        audit_log=Path(os.path.abspath(audit_path)),
        denied_functions=denied_functions,
    )


def merge_engine_config(
    config: WrenConfig,
    policy: ProjectSecurityPolicy,
) -> WrenConfig:
    """Merge project controls without allowing them to weaken global policy."""

    if not policy.enabled:
        return config
    return WrenConfig(
        strict_mode=config.strict_mode or policy.require_mdl_tables,
        denied_functions=(
            config.denied_functions
            | _BASELINE_DENIED_FUNCTIONS
            | policy.denied_functions
        ),
        read_only=config.read_only or policy.read_only,
    )


def discover_security_project(mdl: str | None = None) -> Path | None:
    """Resolve project context for a natural-language or MDL CLI entrypoint."""

    if mdl:
        try:
            candidate = Path(mdl).expanduser()
            if candidate.exists():
                start = candidate if candidate.is_dir() else candidate.parent
                for parent in (start, *start.parents):
                    if (parent / _PROJECT_FILE).exists():
                        return parent
                    if parent == Path.home() or parent == parent.parent:
                        break
        except OSError:
            # Base64 MDL strings and overlong non-path inputs fall back to cwd.
            pass
    try:
        from wren.context import discover_project_path  # noqa: PLC0415

        return discover_project_path()
    except SystemExit:
        return None


def enforce_business_question(
    question: str,
    *,
    project_path: Path | None,
    entrypoint: str,
) -> ProjectSecurityPolicy:
    """Reject a disallowed question before it reaches any semantic tool."""

    policy = load_project_security(project_path)
    if not policy.enabled:
        return policy

    normalized = _normalize(question)
    categories: set[str] = set()
    if policy.prompt_injection_guard:
        if _matches_any(normalized, _INJECTION_PATTERNS):
            categories.add("prompt_injection")
    if policy.business_data_only:
        for category, patterns in (
            ("secret_extraction", _SECRET_PATTERNS),
            ("internal_information", _INTERNAL_PATTERNS),
            ("semantic_layer_bypass", _BYPASS_PATTERNS),
            ("high_risk_execution", _HIGH_RISK_PATTERNS),
        ):
            if _matches_any(normalized, patterns):
                categories.add(category)
        if not categories and not _looks_like_business_question(
            normalized, project_path
        ):
            categories.add("non_business_request")

    decision = "deny" if categories else "allow"
    write_security_audit(
        policy,
        entrypoint=entrypoint,
        decision=decision,
        categories=sorted(categories),
        content=question,
    )
    if categories:
        raise WrenError(
            ErrorCode.SECURITY_POLICY_VIOLATION,
            _REJECTION_MESSAGE,
            phase=ErrorPhase.REQUEST_RECEIVED,
        )
    return policy


def write_security_audit(
    policy: ProjectSecurityPolicy,
    *,
    entrypoint: str,
    decision: str,
    categories: list[str],
    content: str,
) -> bool:
    """Best-effort append of a hash-chained event without retaining raw input.

    Audit persistence is intentionally not an authorization dependency.  A
    failed write is reported through Wren's logger while the already-evaluated
    security policy remains in force.
    """

    if not policy.enabled or policy.audit_log is None:
        return False
    path = policy.audit_log
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        previous_hash = _last_event_hash(path)
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "wren_security_policy_decision",
            "entrypoint": entrypoint,
            "decision": decision,
            "categories": categories,
            "input_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "input_length": len(content),
            "previous_event_hash": previous_hash,
        }
        canonical = json.dumps(
            event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        event["event_hash"] = hashlib.sha256(
            (previous_hash + canonical).encode("utf-8")
        ).hexdigest()
        line = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        _LOGGER.warning(
            "Wren security audit event could not be persisted; "
            "policy enforcement remains active."
        )
        return False


def _last_event_hash(path: Path) -> str:
    if not path.exists():
        return _GENESIS_HASH
    if path.is_symlink():
        raise OSError("audit log must not be a symbolic link")
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size == 0:
            return _GENESIS_HASH
        handle.seek(max(0, size - 65536))
        lines = handle.read().splitlines()
    if not lines:
        return _GENESIS_HASH
    last = json.loads(lines[-1])
    event_hash = last.get("event_hash") if isinstance(last, dict) else None
    if not isinstance(event_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", event_hash):
        raise ValueError("invalid previous audit event")
    return event_hash


def _looks_like_business_question(text: str, project_path: Path | None) -> bool:
    if not text.strip():
        return False
    if _BUSINESS_SIGNAL.search(text):
        return True
    for term in _project_business_terms(project_path):
        if _business_term_matches(term, text):
            return True
    return False


def _project_business_terms(project_path: Path | None) -> set[str]:
    if project_path is None:
        return set()
    terms: set[str] = set()
    for relative in (
        "target/semantic_graph.json",
        "target/ontology_graph.json",
        "target/mdl.json",
    ):
        path = project_path / relative
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        _collect_business_terms(payload, terms)
    return terms


def _collect_business_terms(value: Any, terms: set[str], *, key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _collect_business_terms(child, terms, key=str(child_key).casefold())
        return
    if isinstance(value, list):
        for child in value:
            _collect_business_terms(child, terms, key=key)
        return
    if not isinstance(value, str) or key not in {"name", "label", "synonyms"}:
        return
    normalized = _normalize(value).replace("_", " ")
    if len(normalized) >= 2:
        terms.add(normalized)


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _business_term_matches(term: str, text: str) -> bool:
    if term.isascii():
        escaped = re.escape(term).replace(r"\ ", r"[\s_-]+")
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None
    return term in text or (len(text) >= 2 and text in term)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", normalized)
    # Join CJK characters split by whitespace as a trivial keyword-evasion
    # attempt (for example ``忽 略 之 前 指 令``), while preserving English words.
    for _ in range(4):
        joined = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", normalized)
        if joined == normalized:
            break
        normalized = joined
    return re.sub(r"\s+", " ", normalized).strip()


def _read_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise _configuration_error(f"'security.{key}' must be a YAML boolean")
    return value


def _safe_project_name(value: Any) -> str:
    if not isinstance(value, str):
        return "project"
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")
    return cleaned or "project"


def _configuration_error(message: str) -> WrenError:
    return WrenError(
        ErrorCode.SECURITY_POLICY_VIOLATION,
        f"Invalid project security configuration: {message}.",
        phase=ErrorPhase.VALIDATION,
    )

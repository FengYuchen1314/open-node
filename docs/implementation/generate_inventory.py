#!/usr/bin/env python3
"""Generate and validate the maintained-source inventory for implementation docs."""

from __future__ import annotations

import argparse
import ast
import difflib
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name("source-inventory.md")
SCAN_ROOTS = (
    Path(".github/workflows"),
    Path("backend"),
    Path("frontend"),
    Path("agent"),
    Path("deploy"),
    Path("scripts"),
    Path("probe-worker"),
    Path("runtime"),
    Path("docs/implementation"),
)
ROOT_FILES = (Path("Dockerfile"), Path("install.sh"), Path("uninstall.sh"))
SOURCE_SUFFIXES = {
    ".css",
    ".go",
    ".html",
    ".js",
    ".json",
    ".jsonc",
    ".mjs",
    ".patch",
    ".proto",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SPECIAL_NAMES = {
    "Caddyfile",
    "Caddyfile.dual",
    "Caddyfile.ip",
    ".env.example",
    ".dev.vars.example",
    "nginx.conf.example",
}
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "playwright-report",
}
MAX_SYMBOLS: int | None = None
MAX_DEPENDENCIES = 10
CATEGORY_ORDER = (
    "Backend · 运行代码",
    "Backend · 工程配置",
    "Backend · 测试",
    "Frontend · 运行代码与组件测试",
    "Frontend · 工程配置",
    "Agent · 运行代码",
    "Agent · 工程与主机工具",
    "Agent · 测试",
    "Deploy · 编排与网关",
    "Install · 镜像与生命周期",
    "Scripts · 构建、迁移与验收",
    "Probe Worker",
    "Runtime overlay",
    "CI",
    "实现文档工具",
)


def relative(path: Path) -> Path:
    return path.resolve().relative_to(REPOSITORY)


def included(path: Path) -> bool:
    rel = relative(path)
    if any(part in EXCLUDED_PARTS for part in rel.parts):
        return False
    if rel == Path("docs/implementation/source-inventory.md"):
        return False
    return path.name in SPECIAL_NAMES or path.suffix.lower() in SOURCE_SUFFIXES


def source_files() -> list[Path]:
    found: set[Path] = set()
    for item in ROOT_FILES:
        path = REPOSITORY / item
        if path.is_file():
            found.add(path.resolve())
    for item in SCAN_ROOTS:
        root = REPOSITORY / item
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and included(path):
                found.add(path.resolve())
    return sorted(found, key=lambda path: relative(path).as_posix().lower())


def category(path: Path) -> str:
    value = relative(path).as_posix()
    if value.startswith("backend/app/"):
        return "Backend · 运行代码"
    if value.startswith("backend/tests/"):
        return "Backend · 测试"
    if value.startswith("backend/"):
        return "Backend · 工程配置"
    if value.startswith(("frontend/src/", "frontend/public-probe/")):
        return "Frontend · 运行代码与组件测试"
    if value.startswith("frontend/"):
        return "Frontend · 工程配置"
    if value.startswith("agent/app/"):
        return "Agent · 运行代码"
    if value.startswith("agent/tests/"):
        return "Agent · 测试"
    if value.startswith("agent/"):
        return "Agent · 工程与主机工具"
    if value.startswith("deploy/"):
        return "Deploy · 编排与网关"
    if value in {"Dockerfile", "install.sh", "uninstall.sh"}:
        return "Install · 镜像与生命周期"
    if value.startswith("scripts/"):
        return "Scripts · 构建、迁移与验收"
    if value.startswith("probe-worker/"):
        return "Probe Worker"
    if value.startswith("runtime/"):
        return "Runtime overlay"
    if value.startswith(".github/"):
        return "CI"
    return "实现文档工具"


def kind(path: Path) -> str:
    value = relative(path).as_posix()
    name = path.name
    if "/tests/" in value or re.search(r"(?:^|[._-])test(?:[._-]|$)", name):
        return "测试"
    if name in {"package-lock.json"}:
        return "依赖锁"
    if value.startswith("scripts/vps/smoke-"):
        return "验收脚本"
    if value.startswith("scripts/") or value.endswith("/uninstall.sh") or name in {"install.sh", "uninstall.sh"}:
        return "运维工具"
    if value.startswith(("deploy/", ".github/")) or name in {
        "Dockerfile",
        ".env.example",
        ".dev.vars.example",
    }:
        return "配置"
    if path.suffix.lower() in {".json", ".jsonc", ".toml", ".yaml", ".yml"}:
        return "配置"
    if value.startswith("runtime/"):
        return "运行时补丁"
    return "运行代码"


def feature_name(path: Path) -> str:
    stem = path.name
    for suffix in (".test.tsx", ".test.ts", ".test.mjs", "_test.py", ".tsx", ".ts", ".py", ".sh"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem.replace("_", " ").replace("-", " ")


def responsibility(path: Path) -> str:
    rel = relative(path).as_posix()
    feature = feature_name(path)
    if kind(path) == "测试":
        if rel.startswith("frontend/"):
            return f"{feature} 的组件、状态或客户端契约测试"
        return f"{feature} 的行为、失败路径或安全边界测试"
    if rel == "backend/app/open_node/main.py":
        return "FastAPI 组合根；装配存储、服务、后台任务、中间件与路由"
    if rel == "backend/app/open_node/core/config.py":
        return "环境配置模型、路径和安全默认值的集中校验"
    if rel == "backend/app/open_node/core/authority.py":
        return "精确 Host/:authority 规范化与 HTTP/WebSocket 路由前信任门禁"
    if rel.startswith("backend/app/open_node/api/routes/"):
        return f"{feature} 的 HTTP 路由、鉴权依赖与响应适配"
    if rel.startswith("backend/app/open_node/api/"):
        return "API 路由装配、认证依赖或上传/备份协议边界"
    if rel.startswith("backend/app/open_node/domain/"):
        return f"{feature} 的请求、响应、枚举和领域校验契约"
    if rel.startswith("backend/app/open_node/services/"):
        return f"{feature} 的持久化、业务流程或外部系统适配"
    if rel.startswith("backend/app/open_node/resources/"):
        return "随后端发布的固定资源或校验元数据"
    if rel.startswith("backend/app/"):
        return "后端命令入口、静态站点或进程级辅助逻辑"
    if rel.startswith("frontend/src/domain/"):
        return f"{feature} 的前端类型、纯函数和显示模型"
    if rel.startswith("frontend/src/services/"):
        return f"{feature} 的 API 客户端、响应校验或共享状态"
    if rel.startswith("frontend/src/react/views/"):
        return f"{feature} 路由页面及页面级编排"
    if rel.startswith("frontend/src/react/components/"):
        return f"{feature} 可复用交互组件"
    if rel.startswith("frontend/src/react/hooks/"):
        return f"{feature} React 状态与副作用封装"
    if rel.startswith("frontend/public-probe/"):
        return "独立公开 Probe 页面入口"
    if rel.startswith("frontend/src/"):
        return "前端入口、路由、样式或本地化资源"
    agent_roles = {
        "__main__.py": "Agent 进程入口；装配配置、运行时、日志、日志簿和控制面客户端",
        "client.py": "WebSocket/HTTP 控制面传输、认证、心跳、租约与结果回传",
        "operations.py": "命令分派和 Xray/Nginx/诊断等主机操作门面",
        "journal.py": "命令接收、执行与结果重放的本地 SQLite 日志簿",
        "runtime.py": "Xray 配置校验、原子写入、进程控制与状态读取",
        "service.py": "root-only systemd 安装、升级、回滚、恢复与卸载事务",
        "lifecycle_host.py": "可选 root 生命周期 helper、版本下载与任务日志",
        "lifecycle.py": "非 root Agent 到本机生命周期 helper 的协议客户端",
        "config.py": "Agent 配置模型及安全路径/传输校验",
    }
    if rel.startswith("agent/app/open_node_agent/"):
        return agent_roles.get(path.name, f"Agent 主机侧 {feature} 能力")
    if rel == "agent/uninstall.sh":
        return "交互式 Agent 身份发现、保留卸载与精确彻底清除入口"
    if rel.startswith("agent/"):
        return "Agent 打包、依赖或主机工具配置"
    deploy_roles = {
        "compose.yaml": "控制面应用容器、持久卷、回环端口和加固参数",
        "compose.postgresql.yaml": "可选 PostgreSQL 15 服务与私有数据卷",
        "compose.restore.example.yaml": "隔离恢复实例的示例编排",
        "application_update_helper.py": "面板更新请求到 root install.sh update 的固定功能桥",
        "Caddyfile": "仅域名的受管 HTTPS 网关模板",
        "Caddyfile.ip": "仅公网 IP 的受管 HTTPS 网关模板",
        "Caddyfile.dual": "域名与公网 IP 双入口网关模板",
        "nginx.conf.example": "外部 Nginx TLS 反代安全示例",
        ".env.example": "手工 Compose 部署变量样例",
    }
    if rel.startswith("deploy/"):
        return deploy_roles.get(path.name, "部署编排或网关配置")
    if rel == "Dockerfile":
        return "前端、后端、lego、age 的多阶段构建与非 root 运行镜像"
    if rel == "install.sh":
        return "控制面 fresh install、update、status、setup、uninstall 与 purge 事务入口"
    if rel == "uninstall.sh":
        return "控制面交互式保留卸载或默认彻底清除入口"
    if rel == "scripts/container/entrypoint.sh":
        return "容器启动前目录、恢复状态与运行用户检查"
    if rel.startswith("scripts/container/"):
        return f"镜像构建时获取并校验 {feature} 固定工具"
    if rel.startswith("scripts/ci/"):
        return "CI 测试分片和确定性调度工具"
    if rel.startswith("scripts/migrations/"):
        return "显式、离线且有边界的迁移辅助工具"
    if rel.startswith("scripts/vps/smoke-"):
        return f"{feature} 的 VPS/容器端到端验收"
    if rel.startswith("scripts/vps/"):
        return "VPS 测试环境、发布物构建或同步执行工具"
    if rel.startswith("probe-worker/src/"):
        return "公开 Probe Worker 请求代理、缓存与静态资源服务"
    if rel.startswith("probe-worker/"):
        return "Probe Worker 构建、部署或依赖配置"
    if rel.startswith("runtime/xray/overlay/"):
        return "Open Node Xray fork 的限速/协议能力 overlay"
    if rel.startswith("runtime/xray/"):
        return "固定 Xray 上游应用的可审阅补丁"
    if rel.startswith(".github/"):
        return "持续集成门槛与跨模块回归编排"
    if rel.startswith("docs/implementation/"):
        return "实现说明的源码清单生成与链接校验工具"
    return f"{feature} 工程文件"


def python_details(text: str) -> tuple[list[str], list[str]]:
    tree = ast.parse(text)
    symbols: list[str] = []
    dependencies: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(f"{node.name}.{child.name}")
        elif isinstance(node, ast.Import):
            dependencies.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            dependencies.append(prefix + (node.module or ""))
    local = [
        value
        for value in dependencies
        if value.startswith((".", "open_node", "open_node_agent"))
    ]
    return symbols, local


def script_details(text: str) -> tuple[list[str], list[str]]:
    symbols = re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", text)
    dependencies = re.findall(r"(?m)^\s*(?:source|\.)\s+[\"']?([^\"'\s]+)", text)
    return symbols, dependencies


def typescript_details(text: str) -> tuple[list[str], list[str]]:
    symbol_pattern = re.compile(
        r"(?m)^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
        r"(?:function|class|interface|type|enum|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    )
    symbols = symbol_pattern.findall(text)
    class_pattern = re.compile(
        r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    )
    method_pattern = re.compile(
        r"^\s*(?:(?:public|private|protected|static|abstract|override|async|declare|"
        r"readonly|get|set)\s+)*(?:\*\s*)?([A-Za-z_$][A-Za-z0-9_$]*|constructor)"
        r"\s*(?:<[^>{}]*>)?\s*\("
    )
    active_class: str | None = None
    depth = 0
    for line in text.splitlines():
        if active_class is None:
            match = class_pattern.match(line)
            if match:
                active_class = match.group(1)
                depth = line.count("{") - line.count("}")
            continue
        if depth == 1:
            match = method_pattern.match(line)
            if match:
                symbols.append(f"{active_class}.{match.group(1)}")
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            active_class = None
            depth = 0
    dependencies = re.findall(r"\bfrom\s+[\"'](\.[^\"']+)[\"']", text)
    dependencies += re.findall(r"\bimport\(\s*[\"'](\.[^\"']+)[\"']\s*\)", text)
    return symbols, dependencies


def go_details(text: str) -> tuple[list[str], list[str]]:
    symbols = re.findall(r"(?m)^(?:func\s+(?:\([^)]*\)\s*)?|type\s+)([A-Za-z_][A-Za-z0-9_]*)", text)
    dependencies = [
        value
        for value in re.findall(r"[\"`]([^\"`]+)[\"`]", text)
        if "open-node" in value or "nodelimits" in value
    ]
    return symbols, dependencies


def config_details(path: Path, text: str) -> tuple[list[str], list[str]]:
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return list(value), []
        except json.JSONDecodeError:
            pass
    if path.suffix.lower() == ".toml":
        return re.findall(r"(?m)^\[([^\]]+)\]", text), []
    keys = re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_.-]*):(?:\s|$)", text)
    if keys:
        return keys, []
    return [], []


def details(path: Path, text: str) -> tuple[list[str], list[str]]:
    try:
        if path.suffix.lower() == ".py":
            return python_details(text)
        if path.suffix.lower() == ".sh" or path.name in {"install.sh", "uninstall.sh"}:
            return script_details(text)
        if path.suffix.lower() in {".ts", ".tsx", ".js", ".mjs"}:
            return typescript_details(text)
        if path.suffix.lower() == ".go":
            return go_details(text)
        if path.suffix.lower() in {".json", ".jsonc", ".toml", ".yaml", ".yml"}:
            return config_details(path, text)
    except (SyntaxError, UnicodeError):
        return [], []
    return [], []


def compact(values: list[str], limit: int | None) -> str:
    unique = list(dict.fromkeys(value for value in values if value))
    shown = unique if limit is None else unique[:limit]
    rendered = "<br>".join(f"<code>{html.escape(value)}</code>" for value in shown)
    if limit is not None and len(unique) > limit:
        rendered += f"<br>…另有 {len(unique) - limit} 项"
    return rendered or "—"


def markdown_link(path: Path) -> str:
    rel = relative(path).as_posix()
    target = "../../" + rel
    return f"[{rel}]({target})"


def render() -> str:
    rows: list[dict[str, object]] = []
    for path in source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        symbols, dependencies = details(path, text)
        rows.append(
            {
                "path": path,
                "category": category(path),
                "kind": kind(path),
                "lines": len(text.splitlines()),
                "role": responsibility(path),
                "symbols": symbols,
                "dependencies": dependencies,
            }
        )
    order = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    rows.sort(
        key=lambda row: (
            order.get(str(row["category"]), len(order)),
            relative(row["path"]).as_posix().lower(),
        )
    )
    counts = Counter(str(row["category"]) for row in rows)
    lines = [
        "# 源码清单",
        "",
        "> 本文件由 `python docs/implementation/generate_inventory.py` 生成。",
        "> 职责文字按目录约定生成，符号和本地依赖来自静态分析；它们用于定位代码，不代替人工设计说明。",
        "",
        "## 范围",
        "",
        "清单覆盖维护中的 Backend、Frontend、Agent、部署、安装器、脚本、Probe Worker、",
        "Xray overlay 和 CI 文件。`data/` 保存上游参考源码、构建输入或本地运行数据，",
        "不属于本项目直接维护的实现面，因此不逐文件展开；它的来源边界见",
        "[总体架构](architecture.md#仓库边界)。依赖锁文件计入清单，但不展开其生成内容。",
        "",
        "验证清单是否过期：",
        "",
        "```bash",
        "python docs/implementation/generate_inventory.py --check --check-links",
        "```",
        "",
        "## 统计",
        "",
        "| 模块 | 文件数 |",
        "| --- | ---: |",
    ]
    for name in dict.fromkeys(str(row["category"]) for row in rows):
        lines.append(f"| {name} | {counts[name]} |")
    lines.extend(["", f"合计：{len(rows)} 个文件。", ""])
    active_category = ""
    for row in rows:
        current = str(row["category"])
        if current != active_category:
            active_category = current
            lines.extend(
                [
                    f"## {current}",
                    "",
                    "| 文件 | 类型 | 行数 | 职责 | 关键符号 | 本地依赖 |",
                    "| --- | --- | ---: | --- | --- | --- |",
                ]
            )
        role = str(row["role"]).replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_link(row["path"]),
                    str(row["kind"]),
                    str(row["lines"]),
                    role,
                    compact(row["symbols"], MAX_SYMBOLS),
                    compact(row["dependencies"], MAX_DEPENDENCIES),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 维护规则",
            "",
            "新增、移动或删除实现文件后重新生成本页。生成器只读取源码并覆盖本文件；",
            "`--check` 仅比较生成结果，`--check-links` 只检查 `docs/implementation/` 内本地链接的目标是否存在。",
            "人工职责、调用链、状态机和安全边界应更新对应模块说明，不能只依赖自动清单。",
            "",
        ]
    )
    return "\n".join(lines)


def local_links() -> list[str]:
    failures: list[str] = []
    pattern = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
    for document in sorted(Path(__file__).parent.glob("*.md")):
        text = document.read_text(encoding="utf-8")
        for raw in pattern.findall(text):
            target = raw.strip().split()[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            resolved = (document.parent / file_part).resolve()
            if not resolved.exists():
                failures.append(f"{document.name}: missing {target}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if source-inventory.md is stale")
    parser.add_argument("--check-links", action="store_true", help="check local Markdown link targets")
    args = parser.parse_args()
    expected = render()
    failed = False
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if actual != expected:
            print(f"stale generated inventory: {OUTPUT.relative_to(REPOSITORY)}", file=sys.stderr)
            difference = difflib.unified_diff(
                actual.splitlines(),
                expected.splitlines(),
                fromfile="current",
                tofile="generated",
                n=1,
            )
            for line in list(difference)[:80]:
                print(line, file=sys.stderr)
            failed = True
    else:
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.relative_to(REPOSITORY)}")
    if args.check_links:
        for failure in local_links():
            print(failure, file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

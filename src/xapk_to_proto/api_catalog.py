"""Extract HoH HTTP API catalog + synthetic gRPC service protos from IL2CPP.

Joins ``/game/...`` (and ``/dev/...``) path literals with ``RequestMapping``
factories in dump.cs, writes ``api/endpoints.json``, and emits
``proto/api/*.proto`` service definitions so tools like thesayyn/protoc-gen-ts
can generate gRPC client stubs. Those stubs do **not** speak the game's plain
HTTP ``application/x-protobuf`` transport — use ``endpoints.json`` for path
routing.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HTTP_METHOD = "POST"
EMPTY_TYPE = "google.protobuf.Empty"
EMPTY_IMPORT = "google/protobuf/empty.proto"

# TitleCase from path fails for /dev/cheat* (factories are DevCheat*).
PATH_CALL_OVERRIDES: dict[str, str] = {
    "/dev/cheat": "DevCheat",
    "/dev/cheat/startup": "DevCheatStartup",
}

_NETWORK_REQUEST_RE = re.compile(
    r"public static NetworkRequest<([^>]+)>\s+(\w+)\(([^)]*)\)"
)
_NETWORK_ACTION_RE = re.compile(
    r"public static NetworkAction\s+(\w+)\(([^)]*)\)"
)
_MESSAGE_RE = re.compile(r"^message\s+(\w+)\b", re.MULTILINE)


@dataclass(frozen=True)
class Factory:
    call: str
    kind: str  # "request" | "action"
    request_type: str | None
    response_type: str | None


@dataclass
class Endpoint:
    path: str
    call: str
    service: str
    rpc: str
    http_method: str
    request_type: str | None
    response_type: str | None
    kind: str
    package: str
    proto_file: str


@dataclass
class ApiCatalogResult:
    endpoint_count: int
    service_count: int
    proto_files_written: int
    catalog_path: Path
    proto_api_dir: Path
    warnings: list[str] = field(default_factory=list)


def title_case_kebab(value: str) -> str:
    """``start-and-finish`` / ``collectAll`` → ``StartAndFinish`` / ``CollectAll``."""
    parts: list[str] = []
    for piece in value.split("-"):
        if not piece:
            continue
        parts.append(piece[0].upper() + piece[1:])
    return "".join(parts)


def path_to_call(path: str) -> str:
    """Derive RequestMapping.Call name from an HTTP path."""
    if path in PATH_CALL_OVERRIDES:
        return PATH_CALL_OVERRIDES[path]
    rest = path
    for prefix in ("/game/", "/dev/"):
        if rest.startswith(prefix):
            rest = rest[len(prefix) :]
            break
    else:
        rest = rest.lstrip("/")
    segments: list[str] = []
    for seg in rest.split("/"):
        if not seg:
            continue
        segments.append(title_case_kebab(seg))
    return "".join(segments)


def path_group_key(path: str) -> str:
    """First path segment after ``/game/`` or ``dev`` for ``/dev/...``."""
    if path.startswith("/dev/"):
        return "dev"
    if path.startswith("/game/"):
        rest = path[len("/game/") :]
        segment = rest.split("/", 1)[0]
        return segment or "game"
    rest = path.lstrip("/")
    return rest.split("/", 1)[0] or "game"


def group_to_service_name(group: str) -> str:
    return f"{title_case_kebab(group)}Service"


def group_to_package(group: str) -> str:
    # daily-bonus → hoh.api.daily_bonus.v1
    snake = group.replace("-", "_").lower()
    return f"hoh.api.{snake}.v1"


def group_to_proto_filename(group: str) -> str:
    return f"{group.replace('-', '_')}.proto"


def rpc_name_for(call: str, service_name: str) -> str:
    """Strip the service stem from the Call name when it is a prefix."""
    stem = service_name[: -len("Service")] if service_name.endswith("Service") else service_name
    if stem and call.startswith(stem) and len(call) > len(stem):
        return call[len(stem) :]
    return call


def parse_param_type(params: str) -> str | None:
    """Extract the C# parameter type from a single-parameter signature."""
    params = params.strip()
    if not params:
        return None
    # e.g. "HeroBattleStatsRequest request" or "CheatDTO cheat"
    return params.rsplit(None, 1)[0]


def parse_request_mapping_factories(dump_cs: Path) -> dict[str, Factory]:
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    factories: dict[str, Factory] = {}

    for response, name, params in _NETWORK_REQUEST_RE.findall(text):
        factories[name] = Factory(
            call=name,
            kind="request",
            request_type=parse_param_type(params),
            response_type=response.strip(),
        )

    for name, params in _NETWORK_ACTION_RE.findall(text):
        factories[name] = Factory(
            call=name,
            kind="action",
            request_type=parse_param_type(params),
            response_type=None,
        )

    return factories


def load_api_paths(stringliteral: Path) -> list[str]:
    """Load unique ``/game/...`` and ``/dev/...`` paths from stringliteral.json."""
    data = json.loads(stringliteral.read_text(encoding="utf-8"))
    paths: set[str] = set()
    for entry in data:
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(value, str) and value.startswith(("/game/", "/dev/")):
            paths.add(value)
    return sorted(paths)


def index_message_proto_files(proto_dir: Path) -> dict[str, str]:
    """Map top-level message name → relative proto filename under ``proto_dir``."""
    index: dict[str, str] = {}
    if not proto_dir.is_dir():
        return index
    for path in sorted(proto_dir.glob("*.proto")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in _MESSAGE_RE.findall(text):
            # First file wins; duplicates across files are rare for top-level msgs.
            index.setdefault(name, path.name)
    return index


def build_endpoints(
    paths: list[str],
    factories: dict[str, Factory],
) -> tuple[list[Endpoint], list[str]]:
    warnings: list[str] = []
    endpoints: list[Endpoint] = []
    used_calls: set[str] = set()

    for path in paths:
        call = path_to_call(path)
        factory = factories.get(call)
        if factory is None:
            warnings.append(f"no RequestMapping factory for path {path} (call={call})")
            continue

        group = path_group_key(path)
        service = group_to_service_name(group)
        package = group_to_package(group)
        proto_file = f"api/{group_to_proto_filename(group)}"
        rpc = rpc_name_for(call, service)

        endpoints.append(
            Endpoint(
                path=path,
                call=call,
                service=service,
                rpc=rpc,
                http_method=DEFAULT_HTTP_METHOD,
                request_type=factory.request_type,
                response_type=factory.response_type,
                kind=factory.kind,
                package=package,
                proto_file=proto_file,
            )
        )
        used_calls.add(call)

    for call, factory in sorted(factories.items()):
        if call in used_calls:
            continue
        warnings.append(
            f"RequestMapping factory {call} has no matching /game or /dev path "
            f"(kind={factory.kind})"
        )

    return endpoints, warnings


def _resolve_rpc_collisions(endpoints: list[Endpoint]) -> list[str]:
    """Ensure rpc names are unique per service; fall back to full Call name."""
    warnings: list[str] = []
    by_service: dict[str, list[Endpoint]] = defaultdict(list)
    for ep in endpoints:
        by_service[ep.service].append(ep)

    for service, eps in by_service.items():
        by_rpc: dict[str, list[Endpoint]] = defaultdict(list)
        for ep in eps:
            by_rpc[ep.rpc].append(ep)
        for rpc, group in by_rpc.items():
            calls = {ep.call for ep in group}
            if len(calls) <= 1:
                continue
            for ep in group:
                if ep.rpc != ep.call:
                    warnings.append(
                        f"rpc name collision in {service}: {rpc} -> {ep.call}"
                    )
                    ep.rpc = ep.call
    return warnings


def _pick_rpc_avoiding_messages(
    ep: Endpoint,
    *,
    message_names: set[str],
    used_rpcs: set[str],
) -> str:
    """Choose an RPC name that is not a message type and not already used."""
    candidates = [ep.rpc]
    if ep.call not in candidates:
        candidates.append(ep.call)
    candidates.append(f"{ep.call}Rpc")

    for candidate in candidates:
        if candidate in message_names:
            continue
        if candidate in used_rpcs and candidate != ep.rpc:
            continue
        return candidate
    # Last resort: unique suffix (should be unreachable for real dumps).
    return f"{ep.call}Rpc"


def _resolve_message_name_collisions(
    endpoints: list[Endpoint],
    message_names: set[str],
) -> list[str]:
    """Rename RPCs that collide with protobuf message type names.

    ``rpc CancelContributionRequest(CancelContributionRequest)`` is invalid:
    the input type resolves to the method. Prefer the full Call name, then
    ``{Call}Rpc``.
    """
    if not message_names:
        return []

    warnings: list[str] = []
    by_service: dict[str, list[Endpoint]] = defaultdict(list)
    for ep in endpoints:
        by_service[ep.service].append(ep)

    for service, eps in by_service.items():
        used_rpcs = {ep.rpc for ep in eps}
        for ep in eps:
            if ep.rpc not in message_names:
                continue
            new_rpc = _pick_rpc_avoiding_messages(
                ep,
                message_names=message_names,
                used_rpcs=used_rpcs,
            )
            if new_rpc == ep.rpc:
                warnings.append(
                    f"rpc name collides with message type in {service}: "
                    f"{ep.rpc} (no alternate available)"
                )
                continue
            warnings.append(
                f"rpc name collides with message type in {service}: "
                f"{ep.rpc} -> {new_rpc}"
            )
            used_rpcs.discard(ep.rpc)
            used_rpcs.add(new_rpc)
            ep.rpc = new_rpc
    return warnings


def endpoint_to_catalog_dict(ep: Endpoint) -> dict:
    return {
        "path": ep.path,
        "call": ep.call,
        "service": ep.service,
        "rpc": ep.rpc,
        "http_method": ep.http_method,
        "request_type": ep.request_type,
        "response_type": ep.response_type,
        "kind": ep.kind,
        "package": ep.package,
        "proto_file": ep.proto_file,
    }


def write_endpoints_json(endpoints: list[Endpoint], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "content_type": "application/x-protobuf",
        "default_http_method": DEFAULT_HTTP_METHOD,
        "note": (
            "Synthetic gRPC services map to these HTTP paths. "
            "Generated gRPC clients do not speak this transport; "
            "use this catalog for path routing."
        ),
        "endpoints": [endpoint_to_catalog_dict(ep) for ep in endpoints],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_service_proto(
    *,
    package: str,
    service: str,
    endpoints: list[Endpoint],
    message_files: dict[str, str],
) -> tuple[str, list[str]]:
    """Render one service .proto file. Returns (text, warnings)."""
    warnings: list[str] = []
    imports: set[str] = set()
    need_empty = False

    rpc_lines: list[str] = []
    for ep in sorted(endpoints, key=lambda e: e.rpc):
        req = ep.request_type or EMPTY_TYPE
        resp = ep.response_type or EMPTY_TYPE
        if req == EMPTY_TYPE or resp == EMPTY_TYPE:
            need_empty = True
        for typ in (req, resp):
            if typ == EMPTY_TYPE:
                continue
            filename = message_files.get(typ)
            if filename is None:
                warnings.append(
                    f"{service}.{ep.rpc}: message type {typ} not found under proto/"
                )
                continue
            imports.add(filename)
        rpc_lines.append(f"  rpc {ep.rpc}({req}) returns ({resp});")

    if need_empty:
        imports.add(EMPTY_IMPORT)

    lines = [
        "// Code generated by hoh-protos emit-api. DO NOT EDIT.",
        "// Synthetic gRPC surface for thesayyn/protoc-gen-ts (and similar).",
        "// Live game traffic uses HTTP + application/x-protobuf; see api/endpoints.json.",
        'syntax = "proto3";',
        "",
        f"package {package};",
        "",
    ]
    for imp in sorted(imports):
        lines.append(f'import "{imp}";')
    if imports:
        lines.append("")

    lines.append(f"service {service} {{")
    lines.extend(rpc_lines)
    lines.append("}")
    lines.append("")
    return "\n".join(lines), warnings


def write_service_protos(
    endpoints: list[Endpoint],
    proto_dir: Path,
    message_files: dict[str, str],
) -> tuple[int, list[str]]:
    """Write ``proto/api/*.proto``. Returns (files_written, warnings)."""
    api_dir = proto_dir / "api"
    if api_dir.exists():
        for stale in api_dir.glob("*.proto"):
            stale.unlink()
    api_dir.mkdir(parents=True, exist_ok=True)

    by_file: dict[str, list[Endpoint]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    for ep in endpoints:
        by_file[ep.proto_file].append(ep)
        meta[ep.proto_file] = (ep.package, ep.service)

    warnings: list[str] = []
    written = 0
    for proto_file, eps in sorted(by_file.items()):
        package, service = meta[proto_file]
        text, file_warnings = render_service_proto(
            package=package,
            service=service,
            endpoints=eps,
            message_files=message_files,
        )
        warnings.extend(file_warnings)
        out_path = proto_dir / proto_file
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        written += 1

    return written, warnings


def run_emit_api(
    *,
    dump_cs: Path,
    stringliteral: Path,
    proto_dir: Path,
    catalog_path: Path,
) -> ApiCatalogResult:
    """Build endpoints.json and proto/api/*.proto from IL2CPP artifacts."""
    if not dump_cs.is_file():
        raise FileNotFoundError(f"dump.cs not found: {dump_cs}")
    if not stringliteral.is_file():
        raise FileNotFoundError(f"stringliteral.json not found: {stringliteral}")
    if not proto_dir.is_dir():
        raise FileNotFoundError(f"proto directory not found: {proto_dir}")

    factories = parse_request_mapping_factories(dump_cs)
    if not factories:
        raise ValueError(f"no RequestMapping factories found in {dump_cs}")

    paths = load_api_paths(stringliteral)
    endpoints, warnings = build_endpoints(paths, factories)
    warnings.extend(_resolve_rpc_collisions(endpoints))

    message_files = index_message_proto_files(proto_dir)
    warnings.extend(
        _resolve_message_name_collisions(endpoints, set(message_files))
    )
    proto_count, proto_warnings = write_service_protos(
        endpoints, proto_dir, message_files
    )
    warnings.extend(proto_warnings)

    write_endpoints_json(endpoints, catalog_path)

    services = {ep.service for ep in endpoints}
    return ApiCatalogResult(
        endpoint_count=len(endpoints),
        service_count=len(services),
        proto_files_written=proto_count,
        catalog_path=catalog_path,
        proto_api_dir=proto_dir / "api",
        warnings=warnings,
    )


__all__ = [
    "ApiCatalogResult",
    "Endpoint",
    "Factory",
    "PATH_CALL_OVERRIDES",
    "build_endpoints",
    "group_to_package",
    "group_to_proto_filename",
    "group_to_service_name",
    "index_message_proto_files",
    "load_api_paths",
    "parse_request_mapping_factories",
    "path_group_key",
    "path_to_call",
    "rpc_name_for",
    "run_emit_api",
    "title_case_kebab",
    "write_endpoints_json",
    "write_service_protos",
]

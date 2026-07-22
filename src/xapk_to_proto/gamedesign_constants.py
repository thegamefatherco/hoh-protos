"""Parse GameDesign *Constants classes from dump.cs and emit TypeScript string enums."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

NAMESPACE = "InnoGames.Generated.GameDesign"
CONSTANTS_SUFFIX = "Constants"

_CLASS_HEADER_RE = re.compile(
    r"// Namespace: InnoGames\.Generated\.GameDesign\s*\n"
    r"public static class (\w+Constants)\s//[^\n]*\n"
    r"\{",
)

_STRING_CONST_RE = re.compile(
    r'^\s*public const string (\w+) = "((?:[^"\\]|\\.)*)";\s*$',
    re.MULTILINE,
)


@dataclass
class StringConstant:
    member: str
    value: str


@dataclass
class ConstantsClass:
    class_name: str
    enum_name: str
    constants: list[StringConstant] = field(default_factory=list)


@dataclass
class GamedesignConstantsResult:
    enum_count: int
    files_written: int
    out_dir: Path
    warnings: list[str] = field(default_factory=list)


def enum_name_from_class(class_name: str) -> str:
    if class_name.endswith(CONSTANTS_SUFFIX):
        return class_name[: -len(CONSTANTS_SUFFIX)]
    return class_name


def unescape_csharp_string(raw: str) -> str:
    """Decode a C# string literal body (content between quotes)."""
    out: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "t":
                out.append("\t")
            elif nxt in ('"', "'", "\\"):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return "".join(out)


def escape_ts_string(value: str) -> str:
    """Escape a string for use inside a TypeScript double-quoted literal."""
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _class_body(text: str, open_brace_end: int) -> str | None:
    """Return the body between `{` and matching top-level `}` starting after open_brace_end."""
    depth = 1
    i = open_brace_end
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_end:i]
        i += 1
    return None


def parse_gamedesign_constants(dump_cs: Path) -> tuple[list[ConstantsClass], list[str]]:
    """Parse GameDesign *Constants string holders from an Il2CppDumper dump.cs."""
    text = dump_cs.read_text(encoding="utf-8", errors="replace")
    classes: list[ConstantsClass] = []
    warnings: list[str] = []

    for match in _CLASS_HEADER_RE.finditer(text):
        class_name = match.group(1)
        body = _class_body(text, match.end())
        if body is None:
            warnings.append(f"unclosed class body: {class_name}")
            continue

        constants: list[StringConstant] = []
        for const_match in _STRING_CONST_RE.finditer(body):
            member = const_match.group(1)
            raw_value = const_match.group(2)
            constants.append(
                StringConstant(member=member, value=unescape_csharp_string(raw_value))
            )

        if not constants:
            warnings.append(f"skipping empty constants class: {class_name}")
            continue

        classes.append(
            ConstantsClass(
                class_name=class_name,
                enum_name=enum_name_from_class(class_name),
                constants=constants,
            )
        )

    return classes, warnings


def render_enum_ts(cls: ConstantsClass) -> str:
    lines = [
        "/**",
        f" * Auto-generated from {NAMESPACE}.{cls.class_name}.",
        " * Do not edit by hand.",
        " */",
        f"export enum {cls.enum_name} {{",
    ]
    for const in cls.constants:
        escaped = escape_ts_string(const.value)
        lines.append(f'  {const.member} = "{escaped}",')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def render_index_ts(classes: list[ConstantsClass]) -> str:
    lines = [
        "/**",
        " * Auto-generated GameDesign string enum barrel.",
        " * Do not edit by hand.",
        " */",
        "",
    ]
    for cls in classes:
        lines.append(f'export {{ {cls.enum_name} }} from "./{cls.enum_name}";')
    lines.append("")
    return "\n".join(lines)


def write_gamedesign_constants_ts(
    classes: list[ConstantsClass],
    out_dir: Path,
    warnings: list[str] | None = None,
) -> GamedesignConstantsResult:
    """Write one `.ts` file per enum plus an `index.ts` barrel."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files_written = 0

    for cls in classes:
        path = out_dir / f"{cls.enum_name}.ts"
        path.write_text(render_enum_ts(cls), encoding="utf-8")
        files_written += 1

    index_path = out_dir / "index.ts"
    index_path.write_text(render_index_ts(classes), encoding="utf-8")
    files_written += 1

    return GamedesignConstantsResult(
        enum_count=len(classes),
        files_written=files_written,
        out_dir=out_dir,
        warnings=list(warnings or []),
    )


def run_gamedesign_constants_export(
    dump_cs: Path,
    out_dir: Path,
) -> GamedesignConstantsResult:
    """Parse dump.cs and emit TypeScript string enums under out_dir."""
    if not dump_cs.is_file():
        raise FileNotFoundError(f"dump.cs not found: {dump_cs}")
    classes, warnings = parse_gamedesign_constants(dump_cs)
    return write_gamedesign_constants_ts(classes, out_dir, warnings=warnings)

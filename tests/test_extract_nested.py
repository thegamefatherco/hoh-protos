"""Tests for nested C# type handling in dump.cs parsing."""

from __future__ import annotations

from pathlib import Path

from xapk_to_proto.extract import parse_dump_cs

# Minimal dump section: InnoGames reflection marker + parent message,
# a real Parent.Types.Child nested protobuf type, and a false-positive
# Parent.Child C# nested class that Il2CppDumper tags with IMessage.
_DUMP = """
// Namespace: InnoGames.Generated.Protobuf
public static class SampleReflection // TypeDefIndex: 1
{
}

public sealed class Foo : IMessage<Foo>
{
	public const int IdFieldNumber = 1;
	private int id_; // 0x10
}

public sealed class Foo.Types.Bar : IMessage, IAsyncStateMachine
{
	public const int NameFieldNumber = 1;
	private string name_; // 0x10
}

public class Foo.Bar : IUiData, IEventSystemHandler, IVersioned, IMessage, IAsyncStateMachine
{
	private int m_Index; // 0x10
}
"""


def test_parse_dump_skips_non_types_dotted_names(tmp_path: Path) -> None:
    dump_path = tmp_path / "dump.cs"
    dump_path.write_text(_DUMP, encoding="utf-8")

    protos = parse_dump_cs(dump_path)
    assert "sample.proto" in protos
    pf = protos["sample.proto"]

    top_names = {m.name for m in pf.messages}
    assert "Foo" in top_names
    assert "Foo.Bar" not in top_names
    assert not any("." in m.name for m in pf.messages)

    foo = next(m for m in pf.messages if m.name == "Foo")
    nested_names = {m.name for m in foo.nested_messages}
    assert "Bar" in nested_names
    bar = next(m for m in foo.nested_messages if m.name == "Bar")
    assert any(f.number == 1 for f in bar.fields)

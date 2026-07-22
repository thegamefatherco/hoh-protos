# hoh-protos

Extract `.proto` schemas from Unity **IL2CPP** Android **XAPK** builds that ship **Google.Protobuf** descriptors (built for [Heroes of History](https://github.com/thegamefatherco/hoh-protos) and similar games).

The `hoh-protos` CLI unpacks the XAPK, runs [Il2CppDumper](https://github.com/Perfare/Il2CppDumper), merges embedded and rebuilt `FileDescriptorProto` data, and writes human-readable `.proto` files plus a `descriptors.pb` blob.

## Requirements

- **Python** 3.10 or newer
- A **`.xapk`** from a Unity IL2CPP build that uses Google.Protobuf (embedded descriptors in `global-metadata.dat` and/or types visible in `dump.cs`)
- Network access the first time you run `hoh-protos setup` (downloads .NET 8 and Il2CppDumper into your user cache)

Games that are not IL2CPP or do not use protobuf will fail with a clear error.

## Install

### From a clone (recommended for development)

```bash
git clone https://github.com/thegamefatherco/hoh-protos.git
cd hoh-protos

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e .
```

### From PyPI (when published)

```bash
pip install hoh-protos
```

## One-time setup

Download the bundled .NET runtime and Il2CppDumper (cached under your platform user cache directory, typically `~/.cache/hoh-protos` on Linux):

```bash
hoh-protos setup
```

To refresh cached tools:

```bash
hoh-protos setup --force
```

If you already have **dotnet** and **Il2CppDumper** on your machine, you can point at them instead of using the cache (see [Environment variables](#environment-variables)).

## Quick start

```bash
hoh-protos setup
hoh-protos "/path/to/game.xapk" -o ./output
```

Default output directory is `{xapk_stem}_protos/` next to the XAPK if you omit `-o`.

Example layout after a successful run:

```text
output/
├── descriptors.pb          # merged FileDescriptorSet (game schemas only)
├── descriptors_bundle.pb   # game + Google well-known types (self-contained)
├── il2cpp/
│   └── dump.cs             # Il2CppDumper output
└── proto/
    ├── …                   # one .proto per game descriptor file
    └── google/
        └── protobuf/       # emitted well-known types (any, timestamp, …)
```

Use `-v` for step-by-step logs, `--skip-dump` if `il2cpp/dump.cs` already exists, and `--keep-work` to retain the scratch directory under `output/.work`.

### Using with Buf (TypeScript / other languages)

Generated `.proto` files use **flat imports** (`import "uuid.proto";`, not a package path prefix). Point Buf’s proto root at the directory that contains those files (for example `output/proto` from this tool, or `protos/` after you copy them).

The emitted `proto/` tree is **self-contained**: Google well-known types ship under `proto/google/protobuf/` (from the bundled `well_known.pb`), so you do not need an external dependency for the imports the game protos actually use.

`descriptors_bundle.pb` merges game descriptors with the same well-known set — use it when a tool needs one binary descriptor set instead of resolving imports.

If you copy only the flat game `.proto` files without the `google/` subtree, you must expose Google’s well-known types another way. Without that, Buf reports `imported file does not exist` for `google/protobuf/*.proto`, and every type from those imports fails with `cannot find X in this scope`.

Minimal `buf.yaml` in the consumer repo (no external Google dep when using the full `proto/` tree):

```yaml
version: v2
modules:
  - path: protos
```

If you omit the bundled `google/` subtree, add `buf.build/googleapis/googleapis` under `deps` and run `buf dep update`.

Run `buf lint` / `buf generate`. The `protos` path must contain **all** emitted files (109 game + 6 well-known in a typical Heroes of History extract), not a subset.

Re-run `hoh-protos emit` after upgrading this package: newer emitters repair IL2CPP metadata quirks (`object` placeholders, mis-scoped `Struct`, nested enums, C# `.Types.` segments) so `protoc` and Buf accept the tree.

## Commands

| Command | Purpose |
| --- | --- |
| `hoh-protos setup` | Install .NET 8 + Il2CppDumper into the user cache |
| `hoh-protos run GAME.xapk -o OUT` | Full pipeline (same as default invocation below) |
| `hoh-protos GAME.xapk -o OUT` | Shorthand for `run` |
| `hoh-protos extract --metadata … --dump-cs … --out descriptors.pb` | Build descriptors only |
| `hoh-protos emit --in descriptors.pb --out-dir ./proto` | Render `.proto` files from an existing descriptor set |
| `hoh-protos definitions --descriptors descriptors.pb --input BLOB --out-dir ./definitions` | Decode captured server blobs into per-type JSON |
| `hoh-protos loca --descriptors … --dump-cs … --input loca-compressed --out-dir ./loca` | Decode English loca catalog + display-name maps |
| `hoh-protos gamedesign-constants --dump-cs … --out-dir ./gamedesign/constants` | Emit GameDesign string ID enums as TypeScript |

### Decoding captured server blobs

Heroes of History delivers most game data as `WrappedResponse` envelopes
(`communication.proto`) returned by the `startup`, `wakeup`, and `gamedesign`
endpoints. These contain thousands of `Any`-wrapped DTOs (player state, configs,
and `*DefinitionDTO` definitions). Point the tool at one or more captured blobs to
decode every entry into per-type JSON, one output subdirectory per source:

```bash
hoh-protos run "/path/to/game.xapk" -o ./output \
  --definitions-input ./fixtures/startup.raw \
  --definitions-input ./fixtures/wakeup.raw \
  --definitions-input ./fixtures/gamedesign
```

This writes `output/definitions/<source>/<Type>.json` plus a `manifest.json`
(entry/type counts and any per-type decode warnings) for each blob. The
`--definitions-input` flag is repeatable, and `--definitions-out` overrides the
output directory. Use the standalone `hoh-protos definitions` subcommand to
decode against an existing `descriptors.pb` without re-running the full pipeline.

### English localization (loca)

The game’s main English locale is **`en_DK`** (not `en_US`). Capture the
`LocaCompressed` response (`CompressedLocaResponse` inside a `WrappedResponse`)
and decode it with LocaKeys from `dump.cs`:

```bash
hoh-protos loca \
  --descriptors output/descriptors.pb \
  --dump-cs output/il2cpp/dump.cs \
  --input fixtures/loca-compressed \
  --out-dir output/loca
```

Or during the full pipeline: `--loca-input fixtures/loca-compressed`.

Output:

- `en_DK.json` — full key → string[] catalog (e.g. `"Base.Rarities.Common": ["Common"]`)
- `meta.json` — locale/checksum/version, resolve stats, and `*LocaKey` templates
- `Rarity.ts` (and similar) — typed display-name maps for flat LocaKeys groups
- `index.ts` — barrel for the display-name maps

Proto enums map via templates such as `RarityLocaKey = "Base.Rarities.{0}"`:
`Rarity_COMMON` → `Base.Rarities.Common` → `"Common"`. Gamedesign string IDs like
`equipment_rarity.2` are a **different** namespace and are not auto-joined to
`Base.Rarities.*`.

Each object in a per-type JSON array is a **ProtoJSON `Any` payload**: a flat
`@type` URL plus the message fields. Field names use proto **snake_case**
(`definition_id`, not `definitionId`). This matches nested `google.protobuf.Any`
fields inside messages (e.g. `components[]` on hero definitions).

To load entries in TypeScript with [Protobuf-ES](https://protobufes.com/), build a
schema registry from the same descriptor set used to generate your `_pb.ts` files,
then parse each array item as `google.protobuf.Any`:

```typescript
import { createRegistry, fromJson } from "@bufbuild/protobuf";
import { AnySchema } from "@bufbuild/protobuf/wkt";
// import generated *Schema exports from your buf/protoc output

const registry = createRegistry(/* ...all message schemas... */);

const raw: unknown[] = JSON.parse(await readFile("HeroDefinitionDTO.json", "utf8"));
const heroes = raw.map((item) => fromJson(AnySchema, item, { registry }));
```

The `@type` value (`type.googleapis.com/HeroDefinitionDTO`, etc.) must match the
fully-qualified message names in your registry. If you already know the message
type from the filename, you can also use typed import
(`fromJson(HeroDefinitionDTOSchema, item)`) and ignore the `@type` field.

Note: these blobs are protobuf *wire data*, so they enrich the exported JSON data
only — they cannot recover `.proto` field names, which still come from the XAPK.

Run `hoh-protos --help` or `hoh-protos <command> --help` for flags and examples.

You can also invoke the module directly:

```bash
python -m xapk_to_proto --help
```

## Environment variables

| Variable | Effect |
| --- | --- |
| `XAPK_TO_PROTO_DOTNET` | Path to a `dotnet` executable (skips cached runtime) |
| `XAPK_TO_PROTO_DUMPER` | Path to `Il2CppDumper.dll` (skips cached dumper) |
| `IL2CPP_DUMPER_VERSION` | Il2CppDumper release tag (default: `v6.7.46`) |

## Contributing

Contributions are welcome via [GitHub Issues](https://github.com/thegamefatherco/hoh-protos/issues) and pull requests.

### Development setup

1. Fork and clone the repository.
2. Create a virtual environment and install in editable mode (see [Install](#from-a-clone-recommended-for-development)).
3. Run `hoh-protos setup` so local pipeline runs can execute Il2CppDumper.
4. Make changes under `src/xapk_to_proto/`.

### Code style

- Follow [`.editorconfig`](.editorconfig): UTF-8, LF line endings, **4 spaces** for Python, **2 spaces** for other files.
- The repo’s [VS Code settings](.vscode/settings.json) use **Ruff** for Python formatting on save; matching that locally keeps diffs small.

### Pull requests

1. Branch from `main` with a focused change (one feature or fix per PR when possible).
2. Describe what you changed and how you verified it (e.g. `hoh-protos --help`, a smoke run on a test XAPK if you have one).
3. Do not commit game APKs/XAPKs, extracted IL2CPP binaries, or generated `proto/` trees unless the project explicitly adds fixtures later.

There is no automated test suite yet; manual CLI checks are the expected verification path.

### Reporting bugs

Include Python version, OS, the command you ran, and the error text. Redact paths or package names if needed; avoid attaching copyrighted game assets to public issues.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is **not affiliated** with InnoGames, nor officially connected to the creators or publishers of Heroes of History. All rights to the game, its assets, and intellectual property are owned by their respective holders.

**This project is for educational purposes only and not meant for direct commercial use.** The way you use the data generated by this project is up to you and the maintainers have no responsibility for your actions. Decompiled data from distributables have the same license as the one that you accept when you download or install the respective distributables, unless you were given otherwise written consent from the publishers.

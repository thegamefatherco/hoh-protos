# hoh-protos

Extract `.proto` schemas from Unity **IL2CPP** Android **XAPK** builds that ship **Google.Protobuf** descriptors (built for [Heroes of History](https://github.com/thegamefatherco/hoh-protos) and similar games).

The `hoh-protos` CLI unpacks the XAPK, runs a [v39-capable Il2CppDumper fork](https://github.com/Windows81/Il2CppDumper) (Unity 6000.x / metadata v35–v39; stock [Perfare](https://github.com/Perfare/Il2CppDumper) does not support these), merges embedded and rebuilt `FileDescriptorProto` data, and writes human-readable `.proto` files plus a `descriptors.pb` blob.

## Requirements

- **Python** 3.10 or newer
- A **`.xapk`** from a Unity IL2CPP build that uses Google.Protobuf (embedded descriptors in `global-metadata.dat` and/or types visible in `dump.cs`) — `hoh-protos download-xapk` can fetch one via [apkeep](https://github.com/EFForg/apkeep) (`brew install apkeep` on macOS; otherwise install from that repo)
- Network access the first time you run `hoh-protos setup` (downloads .NET 9 and Il2CppDumper into your user cache)

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

### Optional: asset extraction

`unpack-assets` needs [UnityPy](https://github.com/K0lb3/UnityPy), which is kept
out of the base install because it pulls Pillow and several native wheels that
the proto pipeline does not need:

```bash
pip install 'hoh-protos[assets]'      # or: pip install -e '.[assets]'
```

## One-time setup

Download the bundled .NET runtime and Il2CppDumper (cached under your platform
user cache directory, typically `~/.cache/hoh-protos` on Linux). Install
[apkeep](https://github.com/EFForg/apkeep) yourself: Homebrew on macOS, or
follow the maintainer’s instructions (precompiled binaries or `cargo install`)
on other platforms.

```bash
brew install apkeep   # macOS; elsewhere: https://github.com/EFForg/apkeep
hoh-protos setup
```

To refresh cached tools (required after upgrading this package when the default dumper changes, e.g. for Unity 6 / metadata v39):

```bash
hoh-protos setup --force
```

If you already have **dotnet**, **Il2CppDumper**, or **apkeep** on your machine, you can point at them instead of using the cache (see [Environment variables](#environment-variables)).

## Quick start

```bash
hoh-protos setup
hoh-protos download-fixtures --username USER --password PASS   # → fixtures/{world}/{version}/
hoh-protos run --version 1.50.3
# → output/un0/1.50.3/
```

With `--version`, the pipeline uses `fixtures/{world}/{version}/game.xapk` and
auto-wires `gamedesign`, `loca-compressed`, and `startup` when those files exist.
Default world is `un0` (override with `--world` or `HOH_WORLD`). Explicit path
flags still win when you need a custom layout.

Example layout after a successful run with gamedesign + loca + startup inputs:

```text
output/un0/1.50.3/
├── descriptors.pb          # merged FileDescriptorSet (game schemas only)
├── descriptors_bundle.pb   # game + Google well-known types (self-contained)
├── il2cpp/
│   └── dump.cs             # Il2CppDumper output
├── proto/
│   ├── …                   # one .proto per game descriptor file
│   └── google/
│       └── protobuf/       # emitted well-known types (any, timestamp, …)
├── gamedesign/             # full per-type JSON + constants/
├── startup/                # full decode of the startup blob
└── loca/                   # en_DK catalogs + by_prefix/ splits
```

Providing `--gamedesign-input` (or the fixture default under `--version`) also
runs wirefix (schema repair) and emits GameDesign TypeScript constants under
`gamedesign/constants/`. Use `-v` for step-by-step logs, `--skip-dump` if
`il2cpp/dump.cs` already exists, and `--keep-work` to retain the scratch
directory under `{output}/.work`.

### Using with Buf (TypeScript / other languages)

Generated `.proto` files use **flat imports** (`import "uuid.proto";`, not a package path prefix). Point Buf’s proto root at the directory that contains those files (for example `output/un0/1.50.3/proto` from this tool, or `protos/` after you copy them).

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

Most path-taking commands accept `--version` (and optional `--world`) and resolve
inputs/outputs under `fixtures/{world}/{version}/` and `output/{world}/{version}/`.
Pass an explicit path flag to override any single default.

| Command | Purpose |
| --- | --- |
| `hoh-protos setup` | Install .NET 9 + Il2CppDumper into the user cache |
| `hoh-protos download-fixtures` | Download `game.xapk` + startup/gamedesign/loca under `fixtures/{world}/{version}/` |
| `hoh-protos download-xapk --version V` | Download XAPK → `fixtures/{world}/{version}/game.xapk` |
| `hoh-protos run --version V` | Full pipeline (default output: `output/{world}/{version}/`) |
| `hoh-protos GAME.xapk` | Shorthand for `run` |
| `hoh-protos download-assets --version V` | Download Addressables bundles → `output/.../assets` |
| `hoh-protos unpack-assets --version V` | Extract images → `output/.../unpacked` (default source: `.../assets`) |
| `hoh-protos link-assets --version V` | Resolve `asset_id`-style fields → `output/.../asset_links` |
| `hoh-protos extract --metadata … --version V` | Build descriptors (`--metadata` still required) |
| `hoh-protos emit --version V` | Render `.proto` files from `descriptors.pb` |
| `hoh-protos wirefix --version V` | Correct wire types from the fixture gamedesign blob |
| `hoh-protos gamedesign` / `definitions` / `loca` | Decode captured blobs into per-type JSON / loca |
| `hoh-protos gamedesign-constants --version V` | Emit GameDesign string ID enums as TypeScript |

### Downloading the XAPK

```bash
hoh-protos download-xapk --version 1.50.3                    # → fixtures/un0/1.50.3/game.xapk
hoh-protos download-xapk --version 1.50.3 --world zz0        # → fixtures/zz0/1.50.3/game.xapk
hoh-protos download-xapk -o ./custom/game.xapk               # latest → custom path
```

Prefer `download-fixtures`, which places `game.xapk` next to the server blobs
under `fixtures/{world}/{clientVersion}/`. Use `download-xapk` when you only need
the package.

Downloads go through [apkeep](https://github.com/EFForg/apkeep) (`-d apk-pure`).
On macOS install with `brew install apkeep`; on other platforms install from
the [maintainer repo](https://github.com/EFForg/apkeep) (precompiled binaries
or `cargo install apkeep`). Override the binary with `XAPK_TO_PROTO_APKEEP` if
needed.

A path ending in `.xapk` is used as the filename; anything else is treated as a
directory that receives `{package}_{version}.xapk`. An existing destination is
left alone unless you pass `--force`.

Only the **XAPK** bundle format is available for this package. `--abi` selects
the native split passed to apkeep as `arch=` (default `arm64-v8a`, which is what
the pipeline's `libil2cpp.so` lookup prefers).

### Downloading server fixtures

Log into Heroes of History and save raw protobuf responses plus the matching
XAPK under `fixtures/{world}/{clientVersion}/`:

```text
fixtures/un0/1.50.3/
├── game.xapk
├── gamedesign
├── loca-compressed
└── startup
```

Only InnoGames / APKPure hosts are contacted; nothing is uploaded elsewhere.

```bash
hoh-protos download-fixtures --username USER --password PASS
hoh-protos download-fixtures --world zz0 --only gamedesign,loca
hoh-protos download-fixtures --skip-xapk   # protobuf blobs only
HOH_USERNAME=… HOH_PASSWORD=… hoh-protos download-fixtures -v
```

Credentials: `--username` / `--password`, or env `HOH_USERNAME` / `HOH_PASSWORD`
(**env overwrites flags** when set). Optional `HOH_WORLD` / `HOH_LOCALE` likewise
overwrite `--world` / `--locale`. Default world is `un0` (production); `zz0` /
`zz1` target beta (`beta.heroesgame.com` / `zz0`/`zz1` hosts). Existing files are
left alone unless you pass `--force`.

### Downloading asset bundles

Bundle names are recovered from the Addressables catalog and fetched from the
InnoGames CDN:

```bash
hoh-protos download-assets --version 1.50.3
hoh-protos download-assets --version 1.50.3 --only cleopatra
hoh-protos download-assets --catalog ./catalog.bin -o ./assets --only cleopatra
```

With `--version`, the catalog source defaults to `fixtures/{world}/{version}/game.xapk`
and bundles land in `output/{world}/{version}/assets`. `--xapk` / `--catalog` / `-o`
override those defaults.

`--xapk` reads `assets/aa/catalog.bin` straight out of the nested
`AddressablesAssetPack.apk` without unpacking the ~1 GB archive. Bundles already
present in the output directory are skipped, so re-running the command resumes an
interrupted download; `--clean` wipes the directory first. Downloads run through
a temp file, so an interrupted transfer never leaves a truncated bundle that a
later run would mistake for a finished one.

Other flags: `--only TERM` (substring match, repeatable, overrides `--skip`),
`--skip PREFIX` (default `vfx`, `pfx`), `--jobs` (default 10), `--retries`
(default 2, transient failures only), and `--dry-run` to print the resolved URLs
without downloading. A `manifest.json` records counts and the failed bundle list.

**Expect a low hit rate from an XAPK catalog.** The catalog embedded in the XAPK
describes the bundles that *ship inside the APK*, not the CDN's remote set, and
only ~8% of those names resolve against the CDN. A captured **remote** catalog
(the timestamp-named `catalog_<build-time>.bin` the game fetches at runtime) is
the correct input for CDN downloads, and even then availability drops as bundle
hashes churn between builds. Use `--dry-run` and a narrow `--only` filter before
committing to a full run.

**That caveat applies to CDN downloads only.** The bundles the catalog describes
are already on disk inside the XAPK, under `assets/aa/<platform>/`. If what you
want is images rather than the CDN's remote-only content, skip this command and
use [`unpack-assets`](#unpacking-images-from-asset-bundles) instead.

The full pipeline can do this in one pass with `--assets` (output defaults to
`{output}/assets`), reusing the XAPK it already extracted:

```bash
hoh-protos run --version 1.50.3 --assets
```

### Unpacking images from asset bundles

**For images, do not download from the CDN — unpack the XAPK.** The XAPK ships
the complete Addressables bundle set under `assets/aa/<platform>/` (5,779
bundles in a 1.48 build, of which only 2 have opaque hash names), so unpacking
is offline, complete, and unaffected by the CDN hash churn described above.

```bash
hoh-protos unpack-assets --version 1.50.3 --xapk
hoh-protos unpack-assets --version 1.50.3 --only spriteatlas
```

With `--version` alone, the source defaults to `output/{world}/{version}/assets`
(a prior `download-assets` result) and output to `output/{world}/{version}/unpacked`.
Pass `--xapk` alone with `--version` to stream from `fixtures/.../game.xapk` instead.

`--xapk` streams bundles straight out of the nested `AddressablesAssetPack.apk`,
so the ~1 GB archive is never unpacked. `--bundles` reads a directory instead,
which is how you unpack a `download-assets` result (`download-assets --unpack`
does both in one run). A full 5,779-bundle pass takes roughly 20 seconds on 12
workers and yields ~9,600 PNGs.

Output:

```text
unpacked/
├── extracted/<address>/<asset name>.png
└── index.json
```

Only `Sprite` and `Texture2D` objects are exported. Atlas sheet textures
(`sactx-*`) and textures shadowed by a sprite of the same name are skipped as
redundant — pass `--include-atlas-textures` to keep them. Re-running resumes
from `index.json` rather than redoing finished bundles; `--clean` starts over.

`index.json` carries two lookup tables, because game data references art in two
different ways:

- `by_name` — sprite/texture name to PNG path. A sprite's `m_Name` *is* the
  address the game data uses (`icon_gold_ore_3`), and for icons packed into a
  shared SpriteAtlas this is the only way to find them: they appear nowhere in
  `catalog.bin`.
- `by_bundle_prefix` — Addressables address to its bundle. Addresses like
  `Unit_QueenBoudicca` name a bundle holding a *prefab*, which legitimately has
  no image.

### Linking assets to game data

`link-assets` resolves the asset-reference string fields in decoded definitions
against that index. The fields are discovered from the descriptor set (string
fields matching `asset|icon|sprite|image|portrait|banner|backdrop|…`), so new
builds pick up new fields without a code change.

```bash
hoh-protos run --version 1.50.3 --unpack-assets --link-assets

# or standalone against an existing output tree:
hoh-protos link-assets --version 1.50.3
```

Or in one pipeline run: `--version … --unpack-assets --link-assets`.
`--link-assets` uses the decoded `gamedesign/` (and `startup/` if provided)
automatically — no separate `--definitions-input` required.

This writes `links.json` (per type, per record, per field) and `report.json`
(hit rates per field plus the most common unresolved values). Every value lands
in one of four buckets:

| Status | Meaning |
| --- | --- |
| `image` | Resolved to an extracted PNG |
| `bundle_only` | Address names a bundle holding a prefab/mesh, so there is no image |
| `definition_ref` | Namespaced gamedesign id (`resource.agate`), a different namespace — not a lookup failure |
| `miss` | Genuinely unresolved |

Measured against a 1.48 `gamedesign` capture (23,138 entries, 129 types) with
the full XAPK unpacked: **7,067 asset references — 71% `image`, 10%
`bundle_only`, 8% `definition_ref`, 12% `miss`.** Fields that previously could
not be resolved at all now do, e.g. `PantheonNodeDefinitionDTO.asset_id` at 76/76
and `HeroUnitDefinitionDTO.asset_id` at 899 images with no misses.

Expect `bundle_only` rather than images for `BuildingDefinitionDTO.asset_id`
(479 of 505) and similar prefab references. The remaining misses are mostly
icons that are not in the XAPK at all (`icon_chest_alliance_points*`), i.e.
genuinely remote-only content.

`--gamedesign-input` (and the `gamedesign` / `definitions` commands) decode **all**
types, including buildings, cities, pantheon nodes, and other asset-bearing DTOs.

### Decoding captured server blobs

Heroes of History delivers most game data as `WrappedResponse` envelopes
(`communication.proto`) returned by the `startup`, `wakeup`, and `gamedesign`
endpoints. These contain thousands of `Any`-wrapped DTOs (player state, configs,
and `*DefinitionDTO` definitions). Pass the common fixtures as dedicated inputs:

```bash
hoh-protos run --version 1.50.3
```

This writes:

- `output/un0/1.50.3/gamedesign/` — full per-type JSON + `constants/`
- `output/un0/1.50.3/startup/` — full decode of the startup blob
- `output/un0/1.50.3/loca/` — English catalog

For unusual blobs, `--definitions-input` is still available (repeatable) and
writes under `{output}/{blob-stem}/`. Use the standalone `hoh-protos definitions`
(or `gamedesign`) subcommand to decode against an existing `descriptors.pb`
without re-running the full pipeline.

### English localization (loca)

The game’s main English locale is **`en_DK`** (not `en_US`). Capture the
`LocaCompressed` response (`CompressedLocaResponse` inside a `WrappedResponse`)
and decode it with LocaKeys from `dump.cs`:

```bash
hoh-protos loca --version 1.50.3
```

Or during the full pipeline: `hoh-protos run --version 1.50.3` (auto-wires
`loca-compressed` when present).

Output (flat under `loca/`):

- `en_DK.json` — raw key → `string[]` catalog (game format). Length-1 is a
  single form; length-2 is singular/plural. The client picks with
  `GetText(key, pluralCount)` (English: index 0 when count == 1, else 1).
- `en_DK.i18next.json` — react-i18next flat strings; plurals as
  `key_one` / `key_other`; placeholders as `{{0}}` / `{{duration}}`
  (C# format specs like `:d` / `:s` stripped).
- `en_DK.icu.json` — ICU MessageFormat; plurals as
  `{count, plural, one {…} other {…}}`; placeholders as `{0}` / `{duration}`.
- `en_DK.po` — gettext; `msgctxt` is the loca key; English `nplurals=2`.
- `meta.json` — locale/checksum/version, resolve stats, exported `formats`,
  `prefixes` index, and `*LocaKey` templates
- `by_prefix/{Prefix}.json` — raw catalog split on the first two dotted
  segments (`Base.Rarities`, `Base.Buildings`, …). i18next and ICU copies
  live in `by_prefix/i18next/` and `by_prefix/icu/` (filenames keep the
  suffix). Keys stay fully dotted. Unresolved hashes land in `_unresolved`.
  Use these for lazy `import()` so React does not parse the full ~1.6 MB
  catalog:

```ts
const rarities = await import(
  "./loca/by_prefix/i18next/Base.Rarities.i18next.json"
);
i18n.addResourceBundle("en", "translation", rarities.default, true, true);
t("Base.Rarities.Common");
```

**Placeholders:** game strings use C# `String.Format`-style `{0}`, `{0:d}`,
`{1:s}`, and named ability params like `{duration}`. Modern exports strip
format specs. Duration patterns such as `{0:%d}d {0:hh}h` become `{0}` /
`{{0}}` — format the value in the consumer. Plural `count` (i18next/ICU) /
`n` (gettext) selects the form and is **independent** of which `{0}` is
interpolated (same split as the game’s `pluralCount` vs `parameters`).

**TMP rich text:** tags like `<b>`, `<color=#…>`, `<style=ability_label>`,
`<sprite name=…>`, `<alpha=#60>` are left as-is. StyleSheets / InlineIcons
live in the XAPK `UnityDataAssetPack` Resources but are not extracted by
this tool; resolve or strip them in the consumer.

Proto enum → loca key templates such as `RarityLocaKey = "Base.Rarities.{0}"`
still live in `meta.json`: `Rarity_COMMON` → `Base.Rarities.Common` →
`"Common"`. Gamedesign string IDs like `equipment_rarity.2` are a
**different** namespace and are not auto-joined to `Base.Rarities.*`.
GameDesign ID enums remain under `gamedesign/constants/*.ts` (unchanged).

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
| `XAPK_TO_PROTO_APKEEP` | Path to an `apkeep` executable (skips cached binary / PATH) |
| `XAPK_TO_PROTO_APKEEP_VERSION` | apkeep GitHub release tag for Linux/Windows setup (default: `1.0.0`) |
| `IL2CPP_DUMPER_REPO` | GitHub `owner/repo` for the dumper release (default: `Windows81/Il2CppDumper`) |
| `IL2CPP_DUMPER_VERSION` | Release tag (default: `v20260329T093452Z`) |
| `IL2CPP_DUMPER_ASSET` | Release zip asset name (default: `Il2CppDumper-CLI-20260329T093452Z_0507132.zip`) |

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
2. Describe what you changed and how you verified it (e.g. `hoh-protos --help`,
   `uv run pytest`, a smoke run on a test XAPK if you have one).
3. Do not commit game APKs/XAPKs, extracted IL2CPP binaries, or generated `proto/`
   trees unless the project explicitly adds fixtures later.

Unit tests run with `uv sync --extra test && uv run pytest -m "not acceptance"`.
Acceptance tests (`pytest -m acceptance`) need local
`fixtures/{world}/{version}/` and generated `output/{world}/{version}/` artifacts
and are skipped automatically when those files are missing.

### Reporting bugs

Include Python version, OS, the command you ran, and the error text. Redact paths or package names if needed; avoid attaching copyrighted game assets to public issues.

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).

## Credits

- [Ingweland](https://github.com/ingweland)
  - [HoH Helper Mobile](https://github.com/ingweland/hoh-helper-mobile)
  - [Forge of Games](https://github.com/ingweland/forge-of-games)
- [Il2CppDumper](https://github.com/Windows81/Il2CppDumper) for the IL2CPP decompiler.
- [UnityPy](https://github.com/K0lb3/UnityPy) for the Addressables asset unpacker.

## Disclaimer

This project is **not affiliated** with InnoGames, nor officially connected to the creators or publishers of Heroes of History. All rights to the game, its assets, and intellectual property are owned by their respective holders.

**This project is for educational purposes only and not meant for direct commercial use.** The way you use the data generated by this project is up to you and the maintainers have no responsibility for your actions. Decompiled data from distributables have the same license as the one that you accept when you download or install the respective distributables, unless you were given otherwise written consent from the publishers.

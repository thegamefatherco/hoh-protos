"""Generate a static HTML assets browser for unpacked images.

Writes ``unpacked/preview/{index.html,app.js,style.css}`` with an embedded
manifest so the page works when opened via ``file://`` (no local server, no
``fetch``). Image ``src`` attributes point at ``../extracted/...`` relative to
the preview directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from xapk_to_proto.unpack import INDEX_FILENAME, ImageRecord

PREVIEW_DIRNAME = "preview"

_STYLE_CSS = """\
:root {
  color-scheme: dark;
  --bg: #12141a;
  --panel: #1a1d27;
  --border: #2a2f3d;
  --text: #e8eaef;
  --muted: #9aa3b5;
  --accent: #6ea8fe;
  --accent-dim: #3d5a80;
  --danger: #f07178;
  --thumb: #0d0f14;
  --radius: 6px;
  --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.4;
  min-height: 100%;
}

body {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

header {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  padding: 12px 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  align-items: center;
}

header h1 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

#count {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  flex: 1;
  min-width: 220px;
  align-items: center;
}

input[type="search"],
select {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 7px 10px;
  font: inherit;
  min-width: 0;
}

input[type="search"] {
  flex: 1;
  min-width: 160px;
}

select { max-width: 280px; }

main {
  flex: 1;
  padding: 16px;
}

#status {
  color: var(--muted);
  padding: 24px 8px;
  text-align: center;
}

#grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}

.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  transition: border-color 0.12s ease;
}

.card:hover,
.card:focus-visible {
  border-color: var(--accent);
  outline: none;
}

.thumb {
  aspect-ratio: 1;
  background:
    linear-gradient(45deg, #1c2030 25%, transparent 25%),
    linear-gradient(-45deg, #1c2030 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #1c2030 75%),
    linear-gradient(-45deg, transparent 75%, #1c2030 75%);
  background-size: 12px 12px;
  background-position: 0 0, 0 6px, 6px -6px, -6px 0;
  background-color: var(--thumb);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.thumb img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  image-rendering: auto;
}

.meta {
  padding: 6px 8px 8px;
  border-top: 1px solid var(--border);
  min-height: 2.8em;
}

.meta .name {
  font-size: 11px;
  font-family: var(--mono);
  word-break: break-all;
  color: var(--text);
}

.meta .folder {
  font-size: 10px;
  color: var(--muted);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

#lightbox {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(0, 0, 0, 0.78);
  display: none;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

#lightbox.open { display: flex; }

#lightbox .panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  max-width: min(960px, 100%);
  max-height: 100%;
  overflow: auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
}

@media (min-width: 720px) {
  #lightbox .panel {
    grid-template-columns: minmax(280px, 1fr) minmax(260px, 340px);
  }
}

#lightbox .preview {
  background: var(--thumb);
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  padding: 16px;
}

#lightbox .preview img {
  max-width: 100%;
  max-height: min(70vh, 640px);
  object-fit: contain;
}

#lightbox .details {
  padding: 16px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

@media (min-width: 720px) {
  #lightbox .details { border-top: none; border-left: 1px solid var(--border); }
}

#lightbox h2 {
  margin: 0;
  font-size: 16px;
  font-family: var(--mono);
  word-break: break-all;
}

#lightbox dl {
  margin: 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 10px;
  font-size: 12px;
}

#lightbox dt { color: var(--muted); }
#lightbox dd {
  margin: 0;
  font-family: var(--mono);
  word-break: break-all;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}

button {
  font: inherit;
  cursor: pointer;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  padding: 7px 12px;
}

button.primary {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: #fff;
}

button:hover { border-color: var(--accent); }

#toast {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%) translateY(12px);
  background: #243049;
  color: var(--text);
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: 8px 16px;
  font-size: 13px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease, transform 0.15s ease;
  z-index: 60;
}

#toast.show {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
"""

_APP_JS_TEMPLATE = """\
(() => {
  const ASSETS = __ASSETS_JSON__;
  const PAGE_SIZE = 200;

  const $ = (sel) => document.querySelector(sel);
  const searchEl = $("#search");
  const folderEl = $("#folder");
  const countEl = $("#count");
  const gridEl = $("#grid");
  const statusEl = $("#status");
  const lightbox = $("#lightbox");
  const toastEl = $("#toast");

  let filtered = ASSETS.slice();
  let shown = 0;
  let toastTimer = 0;

  function folders() {
    const set = new Set();
    for (const a of ASSETS) set.add(a.folder);
    return [...set].sort((a, b) => a.localeCompare(b));
  }

  function fillFolderSelect() {
    const opts = ['<option value="">All folders</option>'];
    for (const f of folders()) {
      const label = f || "(root)";
      opts.push(`<option value="${escapeAttr(f)}">${escapeHtml(label)}</option>`);
    }
    folderEl.innerHTML = opts.join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function srcFor(path) {
    return "../" + path.split("/").map(encodeURIComponent).join("/");
  }

  function matches(asset, q, folder) {
    if (folder && asset.folder !== folder) return false;
    if (!q) return true;
    return (
      asset.name.toLowerCase().includes(q) ||
      asset.path.toLowerCase().includes(q) ||
      asset.folder.toLowerCase().includes(q) ||
      (asset.address_prefix || "").toLowerCase().includes(q)
    );
  }

  function applyFilter() {
    const q = (searchEl.value || "").trim().toLowerCase();
    const folder = folderEl.value || "";
    filtered = ASSETS.filter((a) => matches(a, q, folder));
    shown = 0;
    gridEl.innerHTML = "";
    updateCount();
    if (!filtered.length) {
      statusEl.hidden = false;
      statusEl.textContent = ASSETS.length
        ? "No images match the current filter."
        : "No extracted images to preview.";
      return;
    }
    statusEl.hidden = true;
    renderMore();
  }

  function updateCount() {
    countEl.textContent = `${filtered.length.toLocaleString()} / ${ASSETS.length.toLocaleString()}`;
  }

  function cardHtml(asset, index) {
    const dim =
      asset.width && asset.height ? `${asset.width}×${asset.height}` : "";
    return (
      `<article class="card" tabindex="0" data-i="${index}" title="${escapeAttr(asset.path)}">` +
      `<div class="thumb"><img loading="lazy" decoding="async" alt="" src="${escapeAttr(srcFor(asset.path))}"></div>` +
      `<div class="meta"><div class="name">${escapeHtml(asset.name)}</div>` +
      `<div class="folder">${escapeHtml(asset.folder)}${dim ? " · " + dim : ""}</div></div>` +
      `</article>`
    );
  }

  function renderMore() {
    if (shown >= filtered.length) return;
    const end = Math.min(shown + PAGE_SIZE, filtered.length);
    const chunk = [];
    for (let i = shown; i < end; i++) chunk.push(cardHtml(filtered[i], i));
    gridEl.insertAdjacentHTML("beforeend", chunk.join(""));
    shown = end;
  }

  function openLightbox(asset) {
    $("#lb-name").textContent = asset.name;
    $("#lb-img").src = srcFor(asset.path);
    $("#lb-img").alt = asset.name;
    $("#lb-path").textContent = asset.path;
    $("#lb-folder").textContent = asset.folder || "(root)";
    $("#lb-type").textContent = asset.type || "—";
    $("#lb-size").textContent =
      asset.width && asset.height ? `${asset.width} × ${asset.height}` : "—";
    $("#lb-prefix").textContent = asset.address_prefix || "—";
    lightbox.dataset.path = asset.path;
    lightbox.classList.add("open");
  }

  function closeLightbox() {
    lightbox.classList.remove("open");
    $("#lb-img").removeAttribute("src");
  }

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1600);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied");
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        toast("Copied");
      } catch {
        toast("Copy failed");
      }
      ta.remove();
    }
  }

  gridEl.addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (!card) return;
    const asset = filtered[Number(card.dataset.i)];
    if (asset) openLightbox(asset);
  });

  gridEl.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest(".card");
    if (!card) return;
    e.preventDefault();
    const asset = filtered[Number(card.dataset.i)];
    if (asset) openLightbox(asset);
  });

  $("#lb-close").addEventListener("click", closeLightbox);
  lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) closeLightbox();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && lightbox.classList.contains("open")) closeLightbox();
  });

  $("#copy-path").addEventListener("click", () => {
    copyText(lightbox.dataset.path || "");
  });
  $("#copy-rel").addEventListener("click", () => {
    const p = lightbox.dataset.path || "";
    copyText(p ? "../" + p : "");
  });

  let filterTimer = 0;
  searchEl.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(applyFilter, 120);
  });
  folderEl.addEventListener("change", applyFilter);

  window.addEventListener(
    "scroll",
    () => {
      if (shown >= filtered.length) return;
      const nearBottom =
        window.innerHeight + window.scrollY >= document.body.offsetHeight - 600;
      if (nearBottom) renderMore();
    },
    { passive: true }
  );

  fillFolderSelect();
  applyFilter();
})();
"""

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Asset preview</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <h1>Asset preview</h1>
    <span id="count">0 / 0</span>
    <div class="controls">
      <input id="search" type="search" placeholder="Search name, folder, path…" autocomplete="off" spellcheck="false">
      <select id="folder" aria-label="Folder filter"></select>
    </div>
  </header>
  <main>
    <p id="status" hidden></p>
    <div id="grid"></div>
  </main>
  <div id="lightbox" role="dialog" aria-modal="true" aria-label="Image preview">
    <div class="panel">
      <div class="preview"><img id="lb-img" alt=""></div>
      <div class="details">
        <h2 id="lb-name"></h2>
        <dl>
          <dt>Path</dt><dd id="lb-path"></dd>
          <dt>Folder</dt><dd id="lb-folder"></dd>
          <dt>Type</dt><dd id="lb-type"></dd>
          <dt>Size</dt><dd id="lb-size"></dd>
          <dt>Address</dt><dd id="lb-prefix"></dd>
        </dl>
        <div class="actions">
          <button type="button" class="primary" id="copy-path">Copy path</button>
          <button type="button" id="copy-rel">Copy ../ path</button>
          <button type="button" id="lb-close">Close</button>
        </div>
      </div>
    </div>
  </div>
  <div id="toast" role="status" aria-live="polite"></div>
  <script src="app.js"></script>
</body>
</html>
"""


def build_manifest(records: list[ImageRecord]) -> list[dict[str, Any]]:
    """Build a slim, JSON-serializable list of image entries for the browser."""
    out: list[dict[str, Any]] = []
    for record in records:
        path = record.path.replace("\\", "/")
        parts = path.split("/")
        # extracted/<folder>/file.png → folder; odd shapes fall back to "".
        folder = parts[1] if len(parts) >= 3 and parts[0] == "extracted" else (
            "/".join(parts[1:-1]) if len(parts) > 1 else ""
        )
        out.append(
            {
                "name": record.name,
                "path": path,
                "folder": folder,
                "width": record.width,
                "height": record.height,
                "type": record.object_type,
                "address_prefix": record.address_prefix,
            }
        )
    out.sort(key=lambda e: (e["folder"].lower(), e["name"].lower(), e["path"]))
    return out


def write_preview(out_dir: Path, records: list[ImageRecord]) -> Path:
    """Write ``out_dir/preview/`` and return that directory path."""
    preview_dir = out_dir / PREVIEW_DIRNAME
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(records)
    assets_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    app_js = _APP_JS_TEMPLATE.replace("__ASSETS_JSON__", assets_json)

    (preview_dir / "style.css").write_text(_STYLE_CSS, encoding="utf-8")
    (preview_dir / "app.js").write_text(app_js, encoding="utf-8")
    (preview_dir / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    return preview_dir


def write_preview_from_index(index_path: Path) -> tuple[Path, int]:
    """Regenerate the preview from an existing ``index.json`` (or its directory).

    Returns ``(preview_dir, image_count)``.
    """
    path = index_path
    if path.is_dir():
        path = path / INDEX_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"asset index not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[ImageRecord] = []
    for raw in data.get("records", []):
        try:
            record = ImageRecord(**raw)
        except TypeError as e:
            raise ValueError(f"invalid image record in {path}: {raw!r}") from e
        record.path = record.path.replace("\\", "/")
        records.append(record)

    return write_preview(path.parent, records), len(records)


__all__ = [
    "PREVIEW_DIRNAME",
    "build_manifest",
    "write_preview",
    "write_preview_from_index",
]


# llm_batch_api.py â robust batch API for manuscript reworking
# - Derives absolute artifact paths from the manuscript path
# - Keeps filenames per spec: novel_outline.md/json, summary_progress.json, amplification_progress.json
# - Atomic writes for outline/progress files
# - Independent progress for SUMMARY and AMPLIFICATION phases
#
# Public API (as imported by the driver):
#   ensure_outline(manuscript)
#   reset_summary_progress(), _load_summary_progress(), mark_summary_batch_processed(scene_id),
#   get_next_summary_batch_payload(manuscript), set_summary_batch_size(n)
#   reset_progress(), mark_batch_processed(scene_id), get_next_batch_payload(manuscript), set_batch_size(n)
#   upsert_amplified_md(amplified_md_path, scene_id, header, amplified_text)
#   set_first_headers_preview(n)
#
# Requires manuscript_tools.py to provide:
#   load_outline_json(path), save_outline_json(path, scenes, summaries_map),
#   save_outline_md(path, scenes, summaries_map),
#   parse_scenes(text), read_text(path)

from __future__ import annotations
from pathlib import Path
import os, json, tempfile, re
from typing import Dict, List, Tuple, Any

# Defaults (overridable via env)
DEFAULT_SUMMARY_BATCH_SIZE = int(os.environ.get("SUMMARY_BATCH_SIZE", "5"))
DEFAULT_AMPLIFICATION_BATCH_SIZE  = int(os.environ.get("AMPLIFICATION_BATCH_SIZE", "5"))
FIRST_HEADERS_PREVIEW      = int(os.environ.get("FIRST_HEADERS_PREVIEW", "5"))

# Module state
PATHS: Dict[str, str] = {}
_STATE = {
    "summary_batch_size": DEFAULT_SUMMARY_BATCH_SIZE,
    "amplification_batch_size": DEFAULT_AMPLIFICATION_BATCH_SIZE,
    "first_headers_preview": FIRST_HEADERS_PREVIEW,
}

# ---------- path + IO helpers ----------

def _derive_paths(manuscript_path: str) -> Dict[str, str]:
    p = Path(manuscript_path).resolve()
    base = p.parent
    # Filenames per spec (non-namespaced) to avoid breaking existing drivers
    outline_md = base / "novel_outline.md"
    outline_json = base / "novel_outline.json"
    summary_progress = base / "summary_progress.json"
    amplification_progress = base / "amplification_progress.json"
    # Amplified path per spec
    if p.suffix.lower() in (".md", ".markdown"):
        amplified_md = p.with_suffix("").as_posix() + "_amplified.md"
    else:
        amplified_md = (p.as_posix() + "_amplified.md")
    return {
        "manuscript": str(p),
        "outline_md": str(outline_md),
        "outline_json": str(outline_json),
        "summary_progress": str(summary_progress),
        "amplification_progress": str(amplification_progress),
        "amplified_md": amplified_md,
    }

def _ensure_paths_initialized(manuscript_path: str):
    global PATHS
    if not PATHS:
        PATHS = _derive_paths(manuscript_path)

def _safe_write_text(path: str, data: str):
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path_obj.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        os.replace(tmp, str(path_obj))
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def _save_json(path: str, obj: Any):
    _safe_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))

# ---------- preview + batch setters ----------

def set_first_headers_preview(n: int):
    if isinstance(n, int) and n > 0:
        _STATE["first_headers_preview"] = n

def set_summary_batch_size(n: int):
    if isinstance(n, int) and n > 0:
        _STATE["summary_batch_size"] = n
        # propagate to existing progress
        _ensure_paths_initialized(PATHS.get("manuscript") or os.getcwd())
        prog = _load_json(PATHS["summary_progress"], {})
        prog["batch_size"] = n
        _save_json(PATHS["summary_progress"], prog)

def set_batch_size(n: int):
    if isinstance(n, int) and n > 0:
        _STATE["amplification_batch_size"] = n
        _ensure_paths_initialized(PATHS.get("manuscript") or os.getcwd())
        prog = _load_json(PATHS["amplification_progress"], {})
        prog["batch_size"] = n
        _save_json(PATHS["amplification_progress"], prog)

# ---------- outline management ----------

def ensure_outline(manuscript_path: str, preview_count: int | None = None) -> Dict[str, Any]:
    """Create/refresh outline artifacts next to the manuscript.
    Does NOT reset existing progress files.
    """
    _ensure_paths_initialized(manuscript_path)
    from manuscript_tools import load_outline_json, save_outline_json, save_outline_md, parse_scenes, read_text

    text = read_text(PATHS["manuscript"])
    scenes = parse_scenes(text)

    # Load existing outline if present to preserve any already-written summaries
    existing = {}
    if Path(PATHS["outline_json"]).exists():
        try:
            existing = load_outline_json(PATHS["outline_json"])
        except Exception:
            existing = {}

    # Build summaries map aligned to current scenes
    summaries_map = {}
    for s in scenes:
        sid = getattr(s, "scene_id", None) or s.get("scene_id")
        # existing outline might be {scene_id: {"header","summary"}}
        prev = existing.get(sid, {})
        summaries_map[sid] = prev.get("summary", "")

    # Save/refresh outline artifacts
    save_outline_json(PATHS["outline_json"], scenes, summaries_map)
    save_outline_md(PATHS["outline_md"],   scenes, summaries_map)

    return {
        "paths": PATHS.copy(),
        "manuscript": PATHS["manuscript"],
        "preview_count": preview_count if preview_count is not None else _STATE["first_headers_preview"],
    }

# ---------- summary progress ----------

def reset_summary_progress():
    _ensure_paths_initialized(PATHS.get("manuscript") or os.getcwd())
    data = {
        "manuscript_path": PATHS.get("manuscript"),
        "last_scene_id": None,
        "batch_size": _STATE["summary_batch_size"],
    }
    _save_json(PATHS["summary_progress"], data)

def _load_summary_progress():
    _ensure_paths_initialized(PATHS.get("manuscript") or os.getcwd())
    return _load_json(PATHS["summary_progress"], {
        "manuscript_path": PATHS.get("manuscript"),
        "last_scene_id": None,
        "batch_size": _STATE["summary_batch_size"],
    })

def mark_summary_batch_processed(scene_id: str):
    prog = _load_summary_progress()
    prog["last_scene_id"] = scene_id
    _save_json(PATHS["summary_progress"], prog)

def _scene_index_by_id(scenes, scene_id: str) -> int:
    for i, s in enumerate(scenes):
        sid = getattr(s, "scene_id", None) or s.get("scene_id")
        if sid == scene_id:
            return i
    return -1

def _to_item_tuple(scene) -> Tuple[str, str, str]:
    sid = getattr(scene, "scene_id", None) or scene.get("scene_id")
    header = getattr(scene, "header", None) or scene.get("header") or ""
    body = getattr(scene, "body", None) or scene.get("body") or ""
    return sid, header, body

def _outline_window_for(scenes, i: int, outline_json_path: str, radius: int = 1) -> List[Tuple[str, str, str]]:
    # Returns [(scene_id, header, summary), ...] for neighbor scenes
    neighbors = []
    outline = _load_json(outline_json_path, {})
    for j in range(max(0, i - radius), min(len(scenes), i + radius + 1)):
        if j == i:  # skip self
            continue
        s = scenes[j]
        sid, header, _ = _to_item_tuple(s)
        summ = ""
        if isinstance(outline, dict):
            ent = outline.get(sid, {})
            if isinstance(ent, dict):
                summ = ent.get("summary", "") or ""
        neighbors.append((sid, header, summ))
    return neighbors

def get_next_summary_batch_payload(manuscript_path: str) -> Dict[str, Any]:
    _ensure_paths_initialized(manuscript_path)
    from manuscript_tools import parse_scenes, read_text
    text = read_text(PATHS["manuscript"])
    scenes = parse_scenes(text)

    prog = _load_summary_progress()
    batch_size = int(prog.get("batch_size") or _STATE["summary_batch_size"] or 5)

    # Determine start index
    last_id = prog.get("last_scene_id")
    if last_id is None:
        start = 0
    else:
        idx = _scene_index_by_id(scenes, last_id)
        start = idx + 1 if idx >= 0 else 0

    end = min(len(scenes), start + batch_size)
    items = []
    for i in range(start, end):
        sid, header, body = _to_item_tuple(scenes[i])
        items.append({
            "scene_id": sid,
            "header": header,
            "body": body,
            "outline_window": _outline_window_for(scenes, i, PATHS["outline_json"], radius=1),
        })

    next_header = "NONE" if end >= len(scenes) else (_to_item_tuple(scenes[end])[1] or "")
    remaining = max(0, len(scenes) - end)

    return {
        "batch_items": items,
        "progress_hint": {
            "next_scene_header": next_header,
            "remaining_scenes": remaining,
        },
    }

# ---------- amplification progress ----------

def reset_progress():
    _ensure_paths_initialized(PATHS.get("manuscript") or os.getcwd())
    data = {
        "manuscript_path": PATHS.get("manuscript"),
        "last_scene_id": None,
        "batch_size": _STATE["amplification_batch_size"],
    }
    _save_json(PATHS["amplification_progress"], data)

def _load_amplification_progress():
    _ensure_paths_initialized(PATHS.get("manuscript") or os.getcwd())
    return _load_json(PATHS["amplification_progress"], {
        "manuscript_path": PATHS.get("manuscript"),
        "last_scene_id": None,
        "batch_size": _STATE["amplification_batch_size"],
    })

def mark_batch_processed(scene_id: str):
    prog = _load_amplification_progress()
    prog["last_scene_id"] = scene_id
    _save_json(PATHS["amplification_progress"], prog)

def get_next_batch_payload(manuscript_path: str) -> Dict[str, Any]:
    _ensure_paths_initialized(manuscript_path)
    from manuscript_tools import parse_scenes, read_text
    text = read_text(PATHS["manuscript"])
    scenes = parse_scenes(text)

    prog = _load_amplification_progress()
    batch_size = int(prog.get("batch_size") or _STATE["amplification_batch_size"] or 5)

    last_id = prog.get("last_scene_id")
    if last_id is None:
        start = 0
    else:
        idx = _scene_index_by_id(scenes, last_id)
        start = idx + 1 if idx >= 0 else 0

    end = min(len(scenes), start + batch_size)
    items = []
    for i in range(start, end):
        sid, header, body = _to_item_tuple(scenes[i])
        items.append({
            "scene_id": sid,
            "header": header,
            "body": body,
            # For amplification, include neighbor summaries from the outline (produced in Step 2)
            "outline_window": _outline_window_for(scenes, i, PATHS["outline_json"], radius=1),
        })

    next_header = "NONE" if end >= len(scenes) else (_to_item_tuple(scenes[end])[1] or "")
    remaining = max(0, len(scenes) - end)

    return {
        "batch_items": items,
        "progress_hint": {
            "next_scene_header": next_header,
            "remaining_scenes": remaining,
        },
    }

# ---------- amplified MD upsert ----------

def upsert_amplified_md(amplified_md_path: str, scene_id: str, header: str, amplified_text: str):
    """Insert or replace a scene block in the amplified manuscript file.
    The block is delimited by:
      <!-- SCENE:{scene_id}:START -->
      {header verbatim}
      {amplified body}
      <!-- SCENE:{scene_id}:END -->
    """
    path = Path(amplified_md_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    start_tag = f"<!-- SCENE:{scene_id}:START -->"
    end_tag   = f"<!-- SCENE:{scene_id}:END -->"

    new_block = f"{start_tag}\n{header}\n{amplified_text.rstrip()}\n{end_tag}\n"

    if not path.exists():
        _safe_write_text(str(path), new_block)
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        r"(<!--\s*SCENE:" + re.escape(scene_id) + r":START\s*-->)(.*?)(<!--\s*SCENE:" + re.escape(scene_id) + r":END\s*-->)",
        re.DOTALL,
    )
    if pattern.search(content):
        content = pattern.sub(lambda m: new_block, content)
    else:
        # append at end with an extra newline if needed
        if not content.endswith("\n"):
            content += "\n"
        content += new_block

    _safe_write_text(str(path), content)

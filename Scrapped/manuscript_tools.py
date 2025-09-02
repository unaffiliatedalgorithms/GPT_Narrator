# -*- coding: utf-8 -*-
"""
manuscript_tools.py
Utilities to parse a Markdown/Plaintext manuscript into scenes, fetch ranges,
and save/load an outline for LLM-anchored summarization + amplifying.

Recognizes flexible scene headers (case-insensitive, optional bold/italics/###, optional colon).
Examples preserved verbatim:
  **Scene 0.0.0.0:**
  Scene 7:
  # Scene 1.2.3
  ### SCENE A
  Scene X
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any
import re, os, json

# -----------------------
# Data model
# -----------------------
@dataclass
class Scene:
    scene_id: str
    header: str
    body: str
    start_line: int
    end_line: int


# -----------------------
# IO helpers
# -----------------------
def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# -----------------------
# Header detection
# -----------------------
_HEADER_RE = re.compile(
    r"""^\s*
        (?:
            [#]{1,6}\s*|          # optional Markdown heading prefix
            (?:\*\*|__)?          # optional bold start
        )?
        \s*Scene\b
        [\s:-]*
        (?P<id>[A-Za-z0-9._-]+)?  # optional ID token like 0.1.2 or A or 12 (colon excluded)
        \s*[:]?\s*
        (?:
            (?:\*\*|__)?          # optional bold end
        )?
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

def _is_scene_header(line: str) -> Optional[str]:
    """
    Returns the matched ID (possibly None) if the line looks like a scene header.
    """
    m = _HEADER_RE.match(line.strip())
    if not m:
        return None
    return (m.group("id") or "").strip() or None


def _normalize_header(raw: str) -> str:
    """
    Preserve user formatting verbatim; if no explicit '**' present, keep as-is.
    This function does minimal cleanup (strip trailing spaces).
    """
    return raw.rstrip("\n")


# -----------------------
# Parsing
# -----------------------
def parse_scenes(text: str) -> List[Scene]:
    """
    Split the manuscript into scenes using scene headers.
    If a header has no explicit ID, assign IDX#### in order of appearance.
    Prelude text (before the first header) becomes a scene with ID PRELUDE and
    header equal to its first non-empty line (preserved verbatim).
    """
    lines = text.splitlines()
    headers: List[Tuple[int, str, Optional[str]]] = []  # (line_index, header_text, id_or_None)

    for i, ln in enumerate(lines):
        maybe_id = _is_scene_header(ln)
        if maybe_id is not None:
            headers.append((i, ln, maybe_id))

    scenes: List[Scene] = []

    # Prelude (content before first header)
    if not headers:
        # Entire file is a single scene with synthetic header
        first_nonempty = next((ln.strip() for ln in lines if ln.strip()), "Prelude")
        scenes.append(Scene(
            scene_id="PRELUDE",
            header=first_nonempty,
            body="\n".join(lines).strip(),
            start_line=0,
            end_line=len(lines) - 1
        ))
        return scenes

    if headers and headers[0][0] > 0:
        prelude_body = "\n".join(lines[:headers[0][0]]).strip()
        if prelude_body:
            first_nonempty = next((ln.strip() for ln in lines[:headers[0][0]] if ln.strip()), "Prelude")
            scenes.append(Scene(
                scene_id="PRELUDE",
                header=first_nonempty,
                body=prelude_body,
                start_line=0,
                end_line=headers[0][0] - 1
            ))

    # Scene blocks
    auto_idx = 0
    for h_idx, (line_no, header_text, id_token) in enumerate(headers):
        start = line_no + 1
        end = (headers[h_idx + 1][0] - 1) if (h_idx + 1 < len(headers)) else (len(lines) - 1)
        body = "\n".join(lines[start:end + 1]).rstrip()

        # ID selection
        if id_token is None:
            auto_idx += 1
            scene_id = f"IDX{auto_idx:04d}"
        else:
            scene_id = id_token

        scenes.append(Scene(
            scene_id=scene_id,
            header=_normalize_header(header_text),
            body=body,
            start_line=start,
            end_line=end
        ))

    return scenes


# -----------------------
# Outline read/write
# -----------------------
def save_outline_json(path: str, scenes: List[Scene], summaries: Dict[str, str]) -> None:
    data = [
        {"scene_id": s.scene_id, "header": s.header, "summary": summaries.get(s.scene_id, "")}
        for s in scenes
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_outline_md(path: str, scenes: List[Scene], summaries: Dict[str, str]) -> None:
    lines = ["# Novel Outline", ""]
    for s in scenes:
        lines.append(f"## {s.header.strip()}")
        summ = summaries.get(s.scene_id, "").strip() or "_(summary pending)_"
        lines.append("")
        lines.append(summ)
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def load_outline_json(path: str) -> Dict[str, Dict[str, str]]:
    """
    Returns a dict keyed by scene_id with values:
      { "header": str, "summary": str }
    Accepts either the dict format (ours) or a legacy list-of-rows format.
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, dict):
        # Already in keyed form
        return {k: {"header": v.get("header",""), "summary": v.get("summary","")} for k, v in raw.items()}

    # Legacy list
    out: Dict[str, Dict[str, str]] = {}
    for row in raw:
        out[row["scene_id"]] = {"header": row.get("header",""), "summary": row.get("summary", "")}
    return out


# -----------------------
# Windows, indexing, ranges
# -----------------------
def find_index_by_id(scenes: List[Scene], scene_id: str) -> int:
    for i, s in enumerate(scenes):
        if s.scene_id == scene_id:
            return i
    return -1


def get_scene_range(
    scenes: List[Scene],
    start_id: Optional[str] = None,
    end_id: Optional[str] = None,
    count: Optional[int] = None
) -> List[Scene]:
    if not scenes:
        return []
    if start_id is None and end_id is None and count is None:
        return scenes
    if start_id is None and count is not None:
        return scenes[:max(0, count)]

    start_idx = 0 if start_id is None else find_index_by_id(scenes, start_id)

    if end_id is not None:
        end_idx = find_index_by_id(scenes, end_id)
        if end_idx < start_idx:
            start_idx, end_id  # no-op; safeguard
        return scenes[start_idx:end_idx + 1]

    if count is not None:
        return scenes[start_idx:start_idx + max(0, count)]

    return scenes[start_idx:]


def get_outline_window(
    outline: Dict[str, Dict[str, str]],
    scenes: List[Scene],
    center_id: str,
    window: int = 1
) -> List[Tuple[str, str, str]]:
    """
    Returns neighbor metadata around center_id (inclusive):
    [(scene_id, header, summary), ...]
    """
    idx = find_index_by_id(scenes, center_id)
    lo = max(0, idx - window)
    hi = min(len(scenes), idx + window + 1)
    result: List[Tuple[str, str, str]] = []
    for s in scenes[lo:hi]:
        meta = outline.get(s.scene_id, {})
        result.append((s.scene_id, meta.get("header", s.header), meta.get("summary", "")))
    return result


# -----------------------
# Utility
# -----------------------
def scenes_to_markdown(scenes: List[Scene]) -> str:
    """
    Recompose a list of scenes back to markdown. Headers are preserved verbatim.
    """
    parts: List[str] = []
    for s in scenes:
        parts.append(s.header)
        if s.body:
            parts.append(s.body)
        parts.append("")  # blank line between scenes
    return "\n".join(parts).rstrip() + "\n"

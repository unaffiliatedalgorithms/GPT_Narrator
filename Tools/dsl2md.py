# dsl_to_md_multi.py
# Usage: python dsl_to_md_multi.py <input.dsl>

import pathlib
import re
import sys
from lark import Lark

# Your existing parser/transformer module:
#   - dsl_grammar
#   - DSLWithSummary (stores narrative_text content without the triple quotes)
from dsl_extractor import dsl_grammar, DSLWithSummary


TRIPLE_FIELDS = {
    "preamble",
    "arcmap",
    "style",
    "style_adapter",
    "narrative_text",
    "end",
}

def normalize_triple_quote_edge_cases(text: str) -> str:
    """
    Safely fix:
      1) Empty triple-quoted fields accidentally written as "" or ""\"" on their line
      2) Stray bare empty child list: '; []]'  ->  '; summary: []]'
    Only touches exact empties for top-level lines of the known triple-quote fields.
    """
    out_lines = []
    for line in text.splitlines(keepends=False):
        m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$', line)
        if not m:
            out_lines.append(line)
            continue

        key, raw_val = m.group(1), m.group(2)
        if key not in TRIPLE_FIELDS:
            out_lines.append(line)
            continue

        # Look for exact empties (optionally with trailing comma)
        val = raw_val.rstrip(',').strip()
        if val in {'""', '""""'}:
            suffix = ',' if raw_val.rstrip().endswith(',') else ''
            fixed = f'{key}: ' + '""""""' + suffix
            out_lines.append(fixed)
        else:
            out_lines.append(line)

    fixed_text = "\n".join(out_lines)

    # Normalize a common stray leaf: '; []]' -> '; summary: []]'
    fixed_text = re.sub(r';\s*\[\](?=\s*\])', '; summary: []', fixed_text)

    return fixed_text


def parse_block(block_text: str):
    """Normalize edge cases, then parse one Author-block into a doc dict."""
    fixed = normalize_triple_quote_edge_cases(block_text)
    parser = Lark(dsl_grammar, start="start", parser="earley", lexer="dynamic")
    tree = parser.parse(fixed)
    extractor = DSLWithSummary()
    return extractor.transform(tree)


def blocks_from_file(text: str):
    """
    Split the file into 'Author '–prefixed chunks.
    Keeps 'Author ' at the start of each chunk.
    """
    parts = text.split("Author ")
    # If the file doesn't start with "Author ", ignore leading garbage
    return [("Author " + p).strip() for p in parts[1:]]


def path_id_to_dotted(path_id_raw: str) -> str:
    """Turn something like '[0,0,0,0]' into '0.0.0.0' (fallback '?')."""
    if not path_id_raw:
        return "?"
    nums = re.findall(r"-?\d+", path_id_raw)
    return ".".join(nums) if nums else "?"


def main():
    if len(sys.argv) < 2:
        print("Usage: python dsl_to_md_multi.py <input.dsl>")
        sys.exit(1)

    in_path = pathlib.Path(sys.argv[1]).expanduser().resolve()
    text = in_path.read_text(encoding="utf-8")

    raw_blocks = blocks_from_file(text)
    if not raw_blocks:
        print("No 'Author' blocks found.")
        sys.exit(1)

    docs = []
    for i, raw in enumerate(raw_blocks, 1):
        try:
            doc = parse_block(raw)
            docs.append(doc)
        except Exception as e:
            print(f"⚠️ Skipped block #{i} due to parse error: {e}")

    out_path = in_path.with_suffix(".md")
    with out_path.open("w", encoding="utf-8") as f:
        for idx, doc in enumerate(docs, 1):
            scene_id = path_id_to_dotted(doc.get("path_id") or "")
            narrative = (doc.get("narrative_text") or "").rstrip()

            # Scene line in bold
            f.write(f"**Scene {scene_id}:**\n\n")

            # Narrative in a fenced block for legibility
            f.write("```\n")
            f.write(narrative)
            f.write("\n```\n")

            # Blank line between blocks
            if idx != len(docs):
                f.write("\n")

    print(f"✅ Wrote {len(docs)} scene(s) to {out_path}")

if __name__ == "__main__":
    main()

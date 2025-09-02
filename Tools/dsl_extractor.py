# dsl_summary_parser.py
# Minimal-tolerant top-level DSL parser + robust summary tree parser
# - Keeps top-level blocks as raw text (balanced {...} / [...] / """...""")
# - Parses `summary <d[i]=k> [ ... ]` payloads into a proper tree
# - Helpers: summary_tree_depth(), innermost_zoom_in()

from lark import Lark, Transformer, Token
import re
from typing import Any, Dict, List, Tuple, Optional

dsl_grammar = r"""
    // ===== Top level =====
    start: "Author" NAME "{" body "}"       -> document
         | "Author" NAME "{" "}"            -> document_empty

    body: field*
    field: checklist
         | update_rules
         | update_audit
         | preamble
         | arcmap
         | state_block
         | summary_rules
         | path_id
         | summary_block
         | style
         | writing_rules
         | style_adapter
         | narrative_text
         | state_delta
         | end_block

    // ----- Keyword terminals (no trailing colon) -----
    CHECKLIST:      "checklist"
    UPDATE_RULES:   "update_rules"
    UPDATE_AUDIT:   "update_audit"
    PREAMBLE:       "preamble"
    ARCMAP:         "arcmap"
    STATE_KW:       "state"
    SUMMARY_RULES:  "summary_rules"
    PATH_ID:        "path_id"
    SUMMARY_KW:     "summary"
    STYLE:          "style"
    WRITING_RULES:  "writing_rules"
    STYLE_ADAPTER:  "style_adapter"
    NARRATIVE_TEXT: "narrative_text"
    STATE_DELTA:    "state_delta"
    END_KW:         "end"

    // ===== Top-level fields (raw balanced blocks/strings) =====
    checklist:     CHECKLIST ":" object                -> checklist
    update_rules:  UPDATE_RULES ":" array              -> update_rules
    update_audit:  UPDATE_AUDIT ":" (qstring | tstring)-> update_audit

    preamble:      PREAMBLE ":" tstring                -> preamble
    arcmap:        ARCMAP ":"   tstring                -> arcmap
    state_block:   STATE_KW ":" object                 -> state
    summary_rules: SUMMARY_RULES ":" array             -> summary_rules
    path_id:       PATH_ID ":" array                   -> path_id

    style:         STYLE ":" tstring                   -> style
    writing_rules: WRITING_RULES ":" array             -> writing_rules
    style_adapter: STYLE_ADAPTER ":" tstring           -> style_adapter
    narrative_text:NARRATIVE_TEXT ":" tstring          -> narrative_text
    state_delta:   STATE_DELTA ":" object              -> state_delta
    end_block:     END_KW ":" tstring                  -> end

    // ===== Summary blocks (parse fully) =====
    summary_block: SUMMARY_KW summary_selector summary_payload  -> summary

    // Parse selector structurally: <d[INT]=INT>
    summary_selector: "<" "d" "[" INT "]" "=" INT ">"

    // Payload is [ ;-separated fields, order-free; last ';' optional ]
    summary_payload: "[" summary_field_list? "]"
    summary_field_list: summary_field (";" summary_field)* ";"?

    ?summary_field: section_in_parent
                  | parent_section_text
                  | zoom_in
                  | mood
                  | nested_summary

    SECTION_IN_PARENT: "section_in_parent"
    PARENT_SECTION_TEXT: "parent_section_text"
    ZOOM_IN: "zoom_in"
    MOOD: "mood"

    section_in_parent:    SECTION_IN_PARENT ":" INT
    parent_section_text:  PARENT_SECTION_TEXT ":" qstring
    zoom_in:              ZOOM_IN ":" tstring
    mood:                 MOOD ":" tstring

    // Nested summary can be:
    //   - a direct child summary block
    //   - or a list: summary: [ summary ... , summary ... ] (or empty)
    nested_summary: single_nested
                  | summary_list
                  | empty_summary

    empty_summary: "[]"
    single_nested:  summary_block
    summary_list:   SUMMARY_KW ":" "[" (summary_block (","? summary_block)*)? "]"

    // ===== Balanced raw blocks for other fields =====
    object : "{" obj_items? "}"
    obj_items: obj_item+
    ?obj_item: object
             | array
             | tstring
             | qstring
             | chunk

    array  : "[" arr_items? "]"
    arr_items: arr_item+
    ?arr_item: object
             | array
             | tstring
             | qstring
             | chunk

    // triple string: greedy across newlines
    tstring: /\"\"\"(.|\n)*?\"\"\"/s
    // loose quoted string: allows embedded newlines/escapes
    qstring: /"([^"\\]|\\.|\\n)*?"/s

    // everything else (no braces/brackets/quotes/newlines)
    chunk: /[^{}\[\]"\n]+/

    NAME: /[A-Za-z_][A-Za-z0-9_]*/
    INT: /-?(0|[1-9][0-9]*)/

    %import common.WS
    %ignore WS
"""

# ----------------------------
# Helpers
# ----------------------------

def _raw(node: Any) -> str:
    if isinstance(node, Token):
        return str(node)
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_raw(x) for x in node)
    return ""

def _strip_triple(s: str) -> str:
    return s[3:-3] if s.startswith('"""') and s.endswith('"""') else s

def _unquote_qstring(s: str) -> str:
    # s includes quotes; unescape common sequences
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        inner = inner.replace(r'\"', '"').replace(r'\n', '\n').replace(r'\\', '\\')
        return inner
    return s

# ----------------------------
# Transformer
# ----------------------------

class DSLWithSummary(Transformer):
    def __init__(self):
        super().__init__()
        self.doc: Dict[str, Any] = {
            "author": None,
            "checklist": None,
            "update_rules": None,
            "update_audit": None,
            "preamble": None,
            "arcmap": None,
            "state": None,
            "summary_rules": None,
            "path_id": None,
            "summary_tree": [],   # list of top-level summary nodes
            "style": None,
            "writing_rules": None,
            "style_adapter": None,
            "narrative_text": None,
            "state_delta": None,
            "end": None,
        }

    # ---- document ----
    def document(self, children):
        return self.doc

    def document_empty(self, children):
        return self.doc

    def NAME(self, t: Token):
        if self.doc["author"] is None:
            self.doc["author"] = str(t)
        return t

    # ---- top-level fields (raw capture) ----
    def update_audit(self, children):
        # children: [Token('UPDATE_AUDIT', ...), (qstring|tstring)]
        raw = _raw(children[-1])
        if raw.startswith('"""') and raw.endswith('"""'):
            raw = raw[3:-3]
        elif raw.startswith('"') and raw.endswith('"'):
            raw = _unquote_qstring(raw)
        self.doc["update_audit"] = raw
        return raw

    def preamble(self, children):
        # [Token('PREAMBLE', ...), tstring]
        s = _raw(children[-1])
        self.doc["preamble"] = _strip_triple(s)
        return self.doc["preamble"]

    def arcmap(self, children):
        # [Token('ARCMAP', ...), tstring]
        s = _raw(children[-1])
        self.doc["arcmap"] = _strip_triple(s)
        return self.doc["arcmap"]

    def style(self, children):
        # [Token('STYLE', ...), tstring]
        s = _raw(children[-1])
        self.doc["style"] = _strip_triple(s)
        return self.doc["style"]

    def style_adapter(self, children):
        # [Token('STYLE_ADAPTER', ...), tstring]
        s = _raw(children[-1])
        self.doc["style_adapter"] = _strip_triple(s)
        return self.doc["style_adapter"]

    def narrative_text(self, children):
        # [Token('NARRATIVE_TEXT', ...), tstring]
        s = _raw(children[-1])
        self.doc["narrative_text"] = _strip_triple(s)
        return self.doc["narrative_text"]

    def end(self, children):
        # [Token('END_KW', ...), tstring]
        s = _raw(children[-1])
        self.doc["end"] = _strip_triple(s)
        return self.doc["end"]

    def checklist(self, children):
        # [Token('CHECKLIST', ...), object]
        self.doc["checklist"] = _raw(children[-1])
        return self.doc["checklist"]

    def update_rules(self, children):
        # [Token('UPDATE_RULES', ...), array]
        self.doc["update_rules"] = _raw(children[-1])
        return self.doc["update_rules"]

    def state(self, children):
        # [Token('STATE_KW', ...), object]
        self.doc["state"] = _raw(children[-1])
        return self.doc["state"]

    def summary_rules(self, children):
        # [Token('SUMMARY_RULES', ...), array]
        self.doc["summary_rules"] = _raw(children[-1])
        return self.doc["summary_rules"]

    def path_id(self, children):
        # [Token('PATH_ID', ...), array]
        self.doc["path_id"] = _raw(children[-1])
        return self.doc["path_id"]

    def writing_rules(self, children):
        # [Token('WRITING_RULES', ...), array]
        self.doc["writing_rules"] = _raw(children[-1])
        return self.doc["writing_rules"]

    def state_delta(self, children):
        # [Token('STATE_DELTA', ...), object]
        self.doc["state_delta"] = _raw(children[-1])
        return self.doc["state_delta"]

    # ---- summary tree ----
    def summary_selector(self, children):
        d_idx = int(str(children[0]))
        k_val = int(str(children[1]))
        return (d_idx, k_val)

    @staticmethod
    def _extract_selector_pair(items):
        """
        Accepts:
          - [(d_idx, k_val), payload_dict]
          - [Token('SUMMARY_KW', ...), (d_idx, k_val), payload_dict]
          - [Token('SUMMARY_KW', ...), INT, INT, payload_dict]
        Returns (d_idx, k_val) or (None, None).
        """
        # Prefer a tuple if present
        for it in items:
            if isinstance(it, tuple) and len(it) == 2:
                try:
                    return int(it[0]), int(it[1])
                except Exception:
                    pass

        # Otherwise, gather first two INTs
        ints = []

        def maybe_collect(x):
            if isinstance(x, Token) and getattr(x, "type", "") == "INT":
                ints.append(int(str(x)))
            elif isinstance(x, str) and x.strip().lstrip("-").isdigit():
                ints.append(int(x.strip()))

        for it in items:
            if isinstance(it, (list, tuple)):
                for sub in it:
                    maybe_collect(sub)
                    if len(ints) >= 2:
                        break
            else:
                # ignore non-INT tokens (e.g., SUMMARY_KW)
                maybe_collect(it)
            if len(ints) >= 2:
                return ints[0], ints[1]

        return None, None

    def summary(self, children):

        # Drop the leading SUMMARY_KW token if present
        items = [c for c in children if not (isinstance(c, Token) and getattr(c, "type", "") == "SUMMARY_KW")]

        d_idx, k_val = self._extract_selector_pair(items)

        # payload is the last dict produced by summary_payload
        payload = {}
        for ch in reversed(items):
            if isinstance(ch, dict):
                payload = ch
                break

        node = {
            "d_index": d_idx,
            "k_value": k_val,
            "section_in_parent": payload.get("section_in_parent"),
            "parent_section_text": payload.get("parent_section_text"),
            "zoom_in": payload.get("zoom_in"),
            "mood": payload.get("mood"),
            "children": payload.get("children", []),
        }

        node["_built"] = True
        return node

    def empty_summary(self, children):
        return {"children": []}

    def summary_payload(self, children):
        """
        Merge all summary_field results into a single dict with .children.
        Will receive the output of summary_field_list / nested_summary now,
        not Tree(..) objects.
        """

        out = {"children": []}

        def push_child(n):
            if isinstance(n, dict):
                # nested children blob
                if "children" in n and set(n.keys()) == {"children"}:
                    out["children"].extend(n["children"])
                # a built child node (rare path)
                elif n.get("_built"):
                    n.pop("_built", None)
                    out["children"].append(n)
                else:
                    # field dict: section_in_parent / parent_section_text / zoom_in / mood
                    out.update(n)

        for item in children:
            if isinstance(item, list):
                for x in item:
                    push_child(x)
            else:
                push_child(item)

        return out

    def section_in_parent(self, children):
        # children: [Token('SECTION_IN_PARENT', ...), Token('INT', '0')]
        for ch in children:
            if isinstance(ch, Token) and ch.type == 'INT':
                return {"section_in_parent": int(str(ch))}
            if isinstance(ch, str) and ch.strip().lstrip("-").isdigit():
                return {"section_in_parent": int(ch)}
        m = re.search(r'-?\d+', "".join(str(c) for c in children))
        return {"section_in_parent": int(m.group(0)) if m else None}

    def parent_section_text(self, children):
        raw = None
        for ch in reversed(children):
            s = str(ch)
            if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
                raw = s
                break
        if raw is None:
            raw = str(children[-1])
        return {"parent_section_text": _unquote_qstring(raw)}

    def zoom_in(self, children):
        raw = str(children[-1])
        return {"zoom_in": _strip_triple(raw)}

    def mood(self, children):
        raw = str(children[-1])
        return {"mood": _strip_triple(raw)}

    def summary_field_list(self, children):
        """
        Children (after reduction) should be a list of field dicts and/or
        {"children":[...]} blobs from nested_summary. Return as-is so
        summary_payload can merge them.
        """
        return children

    def nested_summary(self, children):
        """
        Unwrap the chosen alternative (single_nested or summary_list) to
        a uniform {"children":[...]} dict.
        """
        # children is usually a 1-item list containing {"children":[...]}
        blob = children[0] if children else {"children":[]}
        if isinstance(blob, dict) and "children" in blob:
            return blob
        # be defensive
        return {"children": blob if isinstance(blob, list) else []}

    def single_nested(self, children):
        """
        Normalize a single child summary node into {"children":[node]}.
        """
        kids = []
        for node in children:
            if isinstance(node, dict) and node.get("_built"):
                node.pop("_built", None)
                kids.append(node)
        return {"children": kids}

    def summary_list(self, children):
        """
        Normalize a possible list of child summary nodes into {"children":[...]}.
        """
        kids = []
        for c in children:
            if isinstance(c, dict) and c.get("_built"):
                c.pop("_built", None)
                kids.append(c)
        return {"children": kids}

    # Capture top-level summaries into doc["summary_tree"]
    def field(self, children):
        if children and isinstance(children[0], dict) and children[0].get("_built"):
            node = children[0]
            node.pop("_built", None)
            self.doc["summary_tree"].append(node)
        return children[0] if children else None

    # Balanced raw blocks used elsewhere
    def object(self, children): return "{" + _raw(children) + "}"
    def array(self, children):  return "[" + _raw(children) + "]"
    def obj_items(self, children): return "".join(_raw(c) for c in children)
    def arr_items(self, children): return "".join(_raw(c) for c in children)
    def tstring(self, children): return _raw(children)
    def qstring(self, children): return _raw(children)
    def chunk(self, children):   return _raw(children)

# ----------------------------
# Summary helpers
# ----------------------------

def summary_tree_depth(nodes: List[Dict[str, Any]]) -> int:
    """
    Max depth of the tree.
    [] -> 0; node with no children -> 1; etc.
    """
    def _depth(node: Dict[str, Any]) -> int:
        if not isinstance(node, dict) or not node:
            return 0
        if not node.get("children"):
            return 1
        return 1 + max(_depth(c) for c in node["children"])
    if not nodes:
        return 0
    return max(_depth(n) for n in nodes)

def innermost_zoom_in(nodes: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Tuple[Optional[int], Optional[int]]]]:
    """
    Follow the first-child chain (active path).
    Returns (zoom_in_text or None, path of (d_index,k_value)).
    Stops cleanly when children == [] (leaf case: 'summary: []').
    """
    if not nodes:
        return None, []
    node = nodes[0]
    path: List[Tuple[Optional[int], Optional[int]]] = [(node.get("d_index"), node.get("k_value"))]
    last_zoom: Optional[str] = node.get("zoom_in")
    while node.get("children"):
        node = node["children"][0]
        path.append((node.get("d_index"), node.get("k_value")))
        if node.get("zoom_in") is not None:
            last_zoom = node["zoom_in"]
    return last_zoom, path

# ----------------------------
# Minimal runner (manual test)
# ----------------------------
if __name__ == "__main__":
    import sys, pathlib
    path = pathlib.Path("test.dsl")
    if len(sys.argv) > 1:
        path = pathlib.Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    parser = Lark(dsl_grammar, start="start", parser="earley", lexer="dynamic")
    tree = parser.parse(text)
    extractor = DSLWithSummary()
    doc = extractor.transform(tree)

    roots = doc["summary_tree"]
    depth = summary_tree_depth(roots)
    zoom, path = innermost_zoom_in(roots)

    print("Author:", doc["author"])
    print("Summary tree depth:", depth)
    print("Innermost path:", path)
    print("Innermost zoom_in (truncated):", (zoom or "")[:200], "...")

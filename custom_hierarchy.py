"""独自の階層定義(JSON)に基づいて DoclingDocument の見出しレベルを付け直す。

素の docling が出力した文書(入力形式は問わない)に対し、
CSV から作った階層 JSON を使って見出し階層を付け直す後段処理。

環境変数:
  HIERARCHY_JSON_PATH        階層JSONのパス (既定: /opt/hierarchy/hierarchy.json)
  HIERARCHY_MODE             "auto" を指定するとJSONを無視し旧挙動(PDF向け自動推定)を使う
                             (未指定でJSONも無い場合は素のdocling出力のまま)
  HIERARCHY_MATCH_THRESHOLD  見出しの一致しきい値 0-1 (既定: 0.85)
  HIERARCHY_LEVEL_OFFSET     JSONのlevelに加算する値 (既定: 0)

階層JSONの形式 (いずれも可):
  1. {"headings": [{"text": "はじめに", "level": 1}, ...]}
  2. [{"text": "はじめに", "level": 1}, ...]
  3. ネスト: [{"text": "はじめに", "children": [{"text": "背景"}]}]

正規表現ルール:
  {"regex": "^\\d+\\.\\d+\\s", "level": 2} のように "text" の代わりに "regex" を書く。
  - 見出し文字列(NFKC正規化済み・大文字小文字無視)に re.search で照合し、一致した見出しに level を適用
  - levelは必須。順序に依存せず、複数ルールが一致する場合はファイル内で先に書いたものが優先
  - "text" エントリと照合できなかった見出しにだけ適用する(text が優先)
  - どのルールにも一致しない見出しは元のレベルのまま

levelの対応 (docling の仕様): TitleItem は "#"、level=N の見出しは N+1 個の "#"。
つまり JSON の level=1 は Markdown の "##" になる。
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

ENV_PATH = "HIERARCHY_JSON_PATH"
ENV_MODE = "HIERARCHY_MODE"
ENV_THRESHOLD = "HIERARCHY_MATCH_THRESHOLD"
ENV_OFFSET = "HIERARCHY_LEVEL_OFFSET"
DEFAULT_PATH = "/opt/hierarchy/hierarchy.json"
DEFAULT_THRESHOLD = 0.85
MAX_LEVEL = 100

_STRIP_RE = re.compile(r"[\s　.,、。・:：()（）\[\]【】\-–—_]+")
_NUM_RE = re.compile(r"^(?:第?\d+[章節項条編部]?|[①-⑳])+")


@dataclass(frozen=True)
class Entry:
    text: str  # regex ルールの場合はパターン文字列
    level: int
    norm: str
    regex: "re.Pattern[str] | None" = None


def normalize(text: str) -> str:
    """表記ゆれ(全半角・空白・句読点・大文字小文字)を吸収した比較用文字列。"""
    return _STRIP_RE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _flatten(node: Any, depth: int, out: list[tuple[str, int, bool]]) -> None:
    if isinstance(node, list):
        for child in node:
            _flatten(child, depth, out)
        return
    if not isinstance(node, dict):
        raise ValueError(f"階層JSONの要素が不正です: {node!r}")
    pattern = node.get("regex", node.get("pattern"))
    text = node.get("text", node.get("title"))
    if pattern is not None and text is not None:
        raise ValueError(f"text と regex は同時に指定できません: {node!r}")
    if pattern is not None:  # 正規表現ルール
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"regexは空でない文字列で指定してください: {node!r}")
        if "level" not in node:
            raise ValueError(f"regexルールにはlevelが必須です: {node!r}")
        if node.get("children"):
            raise ValueError(f"regexルールにchildrenは指定できません: {node!r}")
        label, is_re = pattern, True
    else:
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"見出しテキスト(text)がありません: {node!r}")
        label, is_re = text.strip(), False
    level = node.get("level", depth)
    if isinstance(level, bool) or not isinstance(level, int) or level < 1:
        raise ValueError(f"levelは1以上の整数で指定してください: {node!r}")
    out.append((label, level, is_re))
    children = node.get("children")
    if children:
        _flatten(children, level + 1, out)


def parse_hierarchy(data: Any) -> list[Entry]:
    if isinstance(data, dict) and "headings" in data:
        data = data["headings"]
    items: list[tuple[str, int, bool]] = []
    _flatten(data, 1, items)
    if not items:
        raise ValueError("階層JSONに見出しが1件もありません")
    entries: list[Entry] = []
    for label, level, is_re in items:
        if is_re:
            try:
                rx = re.compile(label, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"正規表現が不正です: {label!r} ({exc})") from exc
            entries.append(Entry(label, level, "", rx))
        else:
            entries.append(Entry(label, level, normalize(label)))
    return entries


_cache: dict[str, tuple[float, list[Entry]]] = {}


def load_hierarchy(path: str) -> list[Entry]:
    """JSONを読み込む。ファイル更新(mtime)を検知するのでコンテナ再起動は不要。"""
    mtime = os.path.getmtime(path)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8-sig") as f:
        entries = parse_hierarchy(json.load(f))
    _cache[path] = (mtime, entries)
    return entries


def _core(norm: str) -> str:
    """先頭の章番号(1.1 / 第2章 / ①)を除いた比較用文字列。"""
    return _NUM_RE.sub("", norm)


def _raw_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    score = SequenceMatcher(None, a, b).ratio()
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 3 and short in long_ and len(short) / len(long_) >= 0.6:
        score = max(score, 0.9)
    return score


def _score(a: str, b: str) -> float:
    score = _raw_score(a, b)
    if score == 1.0:
        return score
    # 章番号の付与有無の差を吸収 (完全一致より僅かに低く評価して優先順位を保つ)
    ca, cb = _core(a), _core(b)
    if ca and cb and (ca != a or cb != b):
        score = max(score, _raw_score(ca, cb) * 0.97)
    return score


def _find(norm: str, entries: list[Entry], used: set[int], cursor: int,
          threshold: float) -> int | None:
    """文書順を優先して最良の一致を探す。まず cursor 以降、なければ全体。"""
    for lo, hi in ((cursor, len(entries)), (0, cursor)):
        best_idx, best = None, threshold
        for i in range(lo, hi):
            if i in used:
                continue
            s = _score(norm, entries[i].norm)
            if s > best:
                best_idx, best = i, s
                if s == 1.0:
                    break
        if best_idx is not None:
            return best_idx
    return None


def apply_to_document(doc: Any, entries: list[Entry], *, threshold: float = DEFAULT_THRESHOLD,
                      level_offset: int = 0) -> dict[str, Any]:
    """見出しアイテムのレベルを階層定義に合わせて更新し、集計を返す。"""
    from docling_core.types.doc import SectionHeaderItem, TitleItem

    text_entries = [e for e in entries if e.regex is None]
    rules = [e for e in entries if e.regex is not None]
    used: set[int] = set()
    used_rules: set[int] = set()
    cursor = 0
    changed = unchanged = 0
    unmatched_doc: list[str] = []

    def _set_level(item: Any, level: int) -> None:
        nonlocal changed, unchanged
        new_level = min(MAX_LEVEL, max(1, level + level_offset))
        if item.level != new_level:
            item.level = new_level
            changed += 1
        else:
            unchanged += 1

    for item, _ in doc.iterate_items():
        if not isinstance(item, (SectionHeaderItem, TitleItem)):
            continue
        # 1) text エントリ(文書順を優先した近似一致)
        idx = _find(normalize(item.text), text_entries, used, cursor, threshold)
        if idx is not None:
            used.add(idx)
            cursor = idx + 1
            if isinstance(item, SectionHeaderItem):  # Title は常に "#"。位置合わせにのみ使う
                _set_level(item, text_entries[idx].level)
            continue
        if isinstance(item, TitleItem):
            continue  # Title は未収録でも正常
        # 2) 正規表現ルール(text で照合できなかった見出しのみ。先に書いたルールが優先)
        heading = unicodedata.normalize("NFKC", item.text).strip()
        for ri, rule in enumerate(rules):
            if rule.regex.search(heading):
                used_rules.add(ri)
                _set_level(item, rule.level)
                break
        else:
            unmatched_doc.append(item.text)

    unmatched_json = [e.text for i, e in enumerate(text_entries) if i not in used]
    unused_rules = [e.text for i, e in enumerate(rules) if i not in used_rules]
    stats = {
        "changed": changed,
        "unchanged": unchanged,
        "unmatched_in_document": unmatched_doc,
        "unmatched_in_json": unmatched_json,
        "unused_regex_rules": unused_rules,
    }
    _log.info("hierarchy applied: changed=%d unchanged=%d doc_unmatched=%d json_unmatched=%d "
              "unused_regex_rules=%d",
              changed, unchanged, len(unmatched_doc), len(unmatched_json), len(unused_rules))
    if unmatched_doc:
        _log.warning("階層JSONに無い見出し(レベルは元のまま): %s", unmatched_doc[:20])
    if unmatched_json:
        _log.warning("文書内で見つからなかったJSON見出し: %s", unmatched_json[:20])
    return stats


def apply_hierarchy(result: Any, source: Any) -> None:
    """manager.py から呼ばれる入口。

    階層JSONがあればそれを適用する。HIERARCHY_MODE=auto の場合のみ旧来の自動推定を使い、
    無い・無効な場合は何もせず素の docling 出力のままにする。
    """
    path = os.environ.get(ENV_PATH, DEFAULT_PATH)
    mode = os.environ.get(ENV_MODE, "").strip().lower()

    if mode == "auto":  # 旧挙動(PDF向け自動推定)を明示的に使いたい場合のみ
        from hierarchical.postprocessor import ResultPostprocessor

        ResultPostprocessor(result, source=source).process()
        return

    if not (path and Path(path).is_file()):
        return  # JSONが無ければ何もしない = 素の docling-serve と同じ出力

    try:
        entries = load_hierarchy(path)
        threshold = float(os.environ.get(ENV_THRESHOLD, DEFAULT_THRESHOLD))
        offset = int(os.environ.get(ENV_OFFSET, "0"))
        apply_to_document(result.document, entries, threshold=threshold,
                          level_offset=offset)
    except Exception:  # 変換自体は止めず、素の出力のまま返す
        _log.exception("階層JSON(%s)の適用に失敗。素のdocling出力のまま返します", path)

"""独自の階層定義(JSON)に基づいて DoclingDocument の見出しレベルを付け直す。

素の docling が出力した文書(入力形式は問わない)に対し、
CSV から作った階層 JSON を使って見出し階層を付け直す後段処理。

環境変数 (推奨: CSVを直接読み込む方式):
  DOCLING_SERVE_HEADING_RULES_ENABLED         true で有効 (1/yes/on も可)。それ以外は何もしない
  DOCLING_SERVE_HEADING_RULES_CSV             ルール表CSVのパス (例: /config/heading_rules.csv)
  DOCLING_SERVE_HEADING_RULES_DOCUMENT_TYPES  適用する document_type をカンマ区切り (既定: all)
                                              "all" のルールは常に適用される。例: all,chapter
  DOCLING_SERVE_HEADING_RULES_MATCH_THRESHOLD text エントリの一致しきい値 0-1 (既定: 0.85)
  DOCLING_SERVE_HEADING_RULES_LEVEL_OFFSET    levelに加算する値 (既定: 0)

  CSVは初回の変換で解析してメモリにキャッシュする。以後はファイルの更新(mtime/サイズ)を
  検知したときだけ再変換する(再起動不要)。CSVが不正なときは変換結果を採用せず、素のdocling
  出力のまま返す(同じ内容のCSVでは再解析もログの連投もしない)。

従来方式 (JSONを直接渡す。上記が有効でないときのみ使用):
  HIERARCHY_JSON_PATH        階層JSONのパス (既定: /opt/hierarchy/hierarchy.json)
  HIERARCHY_MODE             "auto" を指定するとJSON/CSVを無視し旧挙動(PDF向け自動推定)を使う
  HIERARCHY_MATCH_THRESHOLD / HIERARCHY_LEVEL_OFFSET / HIERARCHY_DOCUMENT_TYPES
                             上記 DOCLING_SERVE_HEADING_RULES_* の旧名 (新名が優先)

階層JSONの形式 (いずれも可):
  1. {"headings": [{"text": "はじめに", "level": 1}, ...]}
  2. [{"text": "はじめに", "level": 1}, ...]
  3. ネスト: [{"text": "はじめに", "children": [{"text": "背景"}]}]

正規表現ルール:
  {"regex": "^\\d+\\.\\d+\\s", "level": 2} のように "text" の代わりに "regex" を書く。
  追加項目 (すべて任意):
    name           ルール名 (ログ表示用)
    document_type  適用する文書種別 (既定: "all")
    enabled        false のルールは無視 (既定: true)
    priority       小さいほど先に評価 (既定: 100)。同値はファイル内の順
    ignore_case    true で大文字小文字を区別しない (既定: false)
  - 見出しの文字列を **加工せず**(前後の空白除去のみ) re.search で照合する。
    全角/半角を揃えたい場合はパターン側で両方を書くこと (例: [0-9０-９])。
  - levelは必須。複数ルールが一致する場合は priority の小さい順、同値なら先に書いたもの
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
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

try:  # コンテナ内では docling_jobkit.convert パッケージの兄弟モジュールとして読み込まれる
    from . import csv_to_hierarchy as _csv
except ImportError:  # 単体(トップレベル)で読み込まれた場合
    import csv_to_hierarchy as _csv

_log = logging.getLogger(__name__)

ENV_RULES_ENABLED = "DOCLING_SERVE_HEADING_RULES_ENABLED"
ENV_RULES_CSV = "DOCLING_SERVE_HEADING_RULES_CSV"
ENV_RULES_DOC_TYPES = "DOCLING_SERVE_HEADING_RULES_DOCUMENT_TYPES"
ENV_RULES_THRESHOLD = "DOCLING_SERVE_HEADING_RULES_MATCH_THRESHOLD"
ENV_RULES_OFFSET = "DOCLING_SERVE_HEADING_RULES_LEVEL_OFFSET"
ENV_PATH = "HIERARCHY_JSON_PATH"
ENV_MODE = "HIERARCHY_MODE"
ENV_THRESHOLD = "HIERARCHY_MATCH_THRESHOLD"
ENV_OFFSET = "HIERARCHY_LEVEL_OFFSET"
ENV_DOC_TYPES = "HIERARCHY_DOCUMENT_TYPES"
DEFAULT_PATH = "/opt/hierarchy/hierarchy.json"
DEFAULT_THRESHOLD = 0.85
DEFAULT_PRIORITY = 100
DEFAULT_DOC_TYPE = "all"
MAX_LEVEL = 100

_STRIP_RE = re.compile(r"[\s　.,、。・:：()（）\[\]【】\-–—_]+")
_NUM_RE = re.compile(r"^(?:第?\d+[章節項条編部]?|[①-⑳])+")


@dataclass(frozen=True)
class Entry:
    text: str  # regex ルールの場合はパターン文字列
    level: int
    norm: str
    regex: "re.Pattern[str] | None" = None
    name: str = ""
    document_type: str = DEFAULT_DOC_TYPE
    priority: int = DEFAULT_PRIORITY


def normalize(text: str) -> str:
    """表記ゆれ(全半角・空白・句読点・大文字小文字)を吸収した比較用文字列(text エントリ用)。"""
    return _STRIP_RE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _opt(node: dict, key: str, typ: type, default: Any) -> Any:
    value = node.get(key, default)
    if value is None:
        return default
    if typ is int and isinstance(value, bool):
        raise ValueError(f"{key} は整数で指定してください: {node!r}")
    if not isinstance(value, typ):
        raise ValueError(f"{key} の型が不正です({typ.__name__}が必要): {node!r}")
    return value


def _flatten(node: Any, depth: int, out: list[dict]) -> None:
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
    doc_type = _opt(node, "document_type", str, DEFAULT_DOC_TYPE).strip().lower()
    if not doc_type:
        raise ValueError(f"document_type が空です: {node!r}")
    out.append(
        {
            "label": label,
            "level": level,
            "is_re": is_re,
            "name": _opt(node, "name", str, "").strip(),
            "document_type": doc_type,
            "enabled": _opt(node, "enabled", bool, True),
            "priority": _opt(node, "priority", int, DEFAULT_PRIORITY),
            "ignore_case": _opt(node, "ignore_case", bool, False),
        }
    )
    children = node.get("children")
    if children:
        _flatten(children, level + 1, out)


def parse_hierarchy(data: Any) -> list[Entry]:
    """階層JSONを検証して Entry のリストにする。enabled=false のエントリは除外する。"""
    if isinstance(data, dict) and "headings" in data:
        data = data["headings"]
    items: list[dict] = []
    _flatten(data, 1, items)
    if not items:
        raise ValueError("階層JSONに見出しが1件もありません")
    entries: list[Entry] = []
    for it in items:
        if not it["enabled"]:
            continue  # 無効ルールはコンパイルもしない
        common = dict(
            name=it["name"], document_type=it["document_type"], priority=it["priority"]
        )
        if it["is_re"]:
            try:
                rx = re.compile(it["label"], re.IGNORECASE if it["ignore_case"] else 0)
            except re.error as exc:
                raise ValueError(f"正規表現が不正です: {it['label']!r} ({exc})") from exc
            entries.append(Entry(it["label"], it["level"], "", rx, **common))
        else:
            entries.append(Entry(it["label"], it["level"], normalize(it["label"]), **common))
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


_lock = threading.Lock()
_csv_cache: dict[str, tuple[tuple[int, int], list[Entry]]] = {}
_csv_failed: dict[str, tuple[int, int]] = {}
_MISSING = (-1, -1)


def load_rules_csv(path: str) -> list[Entry] | None:
    """ルール表CSVを読み込んで Entry のリストにする。失敗時は None。

    変換結果はメモリにキャッシュし、ファイルの更新(mtime_ns/サイズ)を検知したときだけ
    再変換する。失敗も同じ内容に対しては一度だけ記録し、以後は再解析しない。
    """
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = _MISSING
    with _lock:
        hit = _csv_cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        if _csv_failed.get(path) == key:
            _log.warning("階層ルールCSV(%s)は前回の読み込みに失敗したため適用しません"
                         "(ファイルを更新すると再試行します)", path)
            return None
        try:
            if key == _MISSING:
                raise FileNotFoundError(path)
            rows = _csv.read_rows(path)
            items = _csv.convert(rows, warn=lambda m: _log.warning("%s", m))
            entries = parse_hierarchy(items)
        except Exception as exc:  # 変換自体は止めず、素の出力のまま返す
            _csv_cache.pop(path, None)
            _csv_failed[path] = key
            _log.error("階層ルールCSV(%s)の読み込みに失敗しました。素のdocling出力のまま返します: %s",
                       path, exc)
            return None
        _csv_failed.pop(path, None)
        _csv_cache[path] = (key, entries)
        _log.info("階層ルールCSVを変換してキャッシュしました: %s (有効 %d 件)", path, len(entries))
        return entries


def clear_caches() -> None:
    """キャッシュを破棄する(テスト用)。"""
    with _lock:
        _csv_cache.clear()
        _csv_failed.clear()
        _cache.clear()
    _warned.clear()


_warned: set[str] = set()


def _env(*names: str, default: str | None = None) -> str | None:
    """最初に見つかった空でない環境変数の値(前後の空白除去済み)。"""
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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
                      level_offset: int = 0,
                      document_types: Iterable[str] = (DEFAULT_DOC_TYPE,)) -> dict[str, Any]:
    """見出しアイテムのレベルを階層定義に合わせて更新し、集計を返す。"""
    from docling_core.types.doc import SectionHeaderItem, TitleItem

    types = {t.strip().lower() for t in document_types if t and t.strip()}
    types.add(DEFAULT_DOC_TYPE)  # "all" は常に適用
    active = [e for e in entries if e.document_type in types]
    text_entries = [e for e in active if e.regex is None]
    # priority の小さい順(同値は元の順。sorted は安定ソート)
    rules = sorted((e for e in active if e.regex is not None), key=lambda e: e.priority)

    used: set[int] = set()
    cursor = 0
    changed = unchanged = 0
    unmatched_doc: list[str] = []
    rule_hits: Counter[str] = Counter()

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
        # 2) 正規表現ルール(text で照合できなかった見出しのみ)。生の文字列で照合する
        heading = item.text.strip()
        for rule in rules:
            if rule.regex.search(heading):
                rule_hits[rule.name or rule.text] += 1
                _set_level(item, rule.level)
                break
        else:
            unmatched_doc.append(item.text)

    unmatched_json = [e.text for i, e in enumerate(text_entries) if i not in used]
    unused_rules = [(e.name or e.text) for e in rules if (e.name or e.text) not in rule_hits]
    stats = {
        "changed": changed,
        "unchanged": unchanged,
        "unmatched_in_document": unmatched_doc,
        "unmatched_in_json": unmatched_json,
        "unused_regex_rules": unused_rules,
        "rule_hits": dict(rule_hits),
    }
    _log.info("hierarchy applied: changed=%d unchanged=%d doc_unmatched=%d json_unmatched=%d "
              "unused_regex_rules=%d rule_hits=%s",
              changed, unchanged, len(unmatched_doc), len(unmatched_json), len(unused_rules),
              dict(rule_hits))
    if unmatched_doc:
        _log.warning("階層JSONに無い見出し(レベルは元のまま): %s", unmatched_doc[:20])
    if unmatched_json:
        _log.warning("文書内で見つからなかったJSON見出し: %s", unmatched_json[:20])
    return stats


def apply_hierarchy(result: Any, source: Any) -> None:
    """manager.py から呼ばれる入口。

    優先順位:
      1. HIERARCHY_MODE=auto                      旧来のPDF向け自動推定
      2. DOCLING_SERVE_HEADING_RULES_ENABLED=true ルール表CSVを読み込んで適用(キャッシュあり)
      3. 階層JSON(HIERARCHY_JSON_PATH)が存在      従来方式
      4. 上記いずれも無い                         何もしない = 素の docling 出力
    いかなる失敗でも変換は止めず、素の出力のまま返す。
    """
    mode = os.environ.get(ENV_MODE, "").strip().lower()
    if mode == "auto":  # 旧挙動(PDF向け自動推定)を明示的に使いたい場合のみ
        from hierarchical.postprocessor import ResultPostprocessor

        ResultPostprocessor(result, source=source).process()
        return

    csv_path = _env(ENV_RULES_CSV)
    try:
        if _truthy(_env(ENV_RULES_ENABLED)):
            if not csv_path:
                if "no_csv" not in _warned:
                    _warned.add("no_csv")
                    _log.error("%s=true ですが %s が未設定です。階層ルールは適用しません",
                               ENV_RULES_ENABLED, ENV_RULES_CSV)
                return
            entries = load_rules_csv(csv_path)
            if entries is None:
                return
        else:
            if csv_path and "disabled" not in _warned:
                _warned.add("disabled")
                _log.warning("%s が設定されていますが %s が true ではないため、CSVは使用しません",
                             ENV_RULES_CSV, ENV_RULES_ENABLED)
            json_path = os.environ.get(ENV_PATH, DEFAULT_PATH)
            if not (json_path and Path(json_path).is_file()):
                return  # 何も無ければ素の docling-serve と同じ出力
            entries = load_hierarchy(json_path)

        threshold = float(_env(ENV_RULES_THRESHOLD, "HIERARCHY_MATCH_THRESHOLD",
                               default=str(DEFAULT_THRESHOLD)))
        offset = int(_env(ENV_RULES_OFFSET, "HIERARCHY_LEVEL_OFFSET", default="0"))
        doc_types = (_env(ENV_RULES_DOC_TYPES, ENV_DOC_TYPES, default=DEFAULT_DOC_TYPE)
                     or DEFAULT_DOC_TYPE).split(",")
        apply_to_document(result.document, entries, threshold=threshold,
                          level_offset=offset, document_types=doc_types)
    except Exception:  # 変換自体は止めず、素の出力のまま返す
        _log.exception("階層ルールの適用に失敗。素のdocling出力のまま返します")

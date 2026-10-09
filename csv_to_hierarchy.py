#!/usr/bin/env python3
r"""階層CSVを階層JSON(custom_hierarchy.py 用)に変換する。

対応するCSV形式:
  R) ルール表: ヘッダに regex と level 列がある (他の列は任意)
       name,level,document_type,regex,enabled,priority
       共通_大項目,1,all,"^[0-9０-９]+[.．][ 　\t]+[^0-9０-９\s].+$",true,100
       章型_細別項目,2,chapter,"^[0-9０-９]+[.．][0-9０-９]+[ 　\t]+.+$",true,100
     - name:          ルール名 (ログ表示用)
     - level:         見出しレベル (1以上)
     - document_type: 適用する文書種別 (空なら all)
     - regex:         正規表現 (見出し文字列を加工せずに re.search で照合)
     - enabled:       true/false (true,1,yes,on,有効,はい,○ / false,0,no,off,無効,いいえ,×。空はtrue)
     - priority:      小さいほど先に評価 (空なら 100)。同値はCSVの順
     - promote:       (任意の列) false にするとそのルールは本文を昇格しない。空/列なしは既定(昇格する)に従う
     enabled=false の行もJSONには出力される(読み込み時に無視される)。全行の正規表現を検証する。
  A) 縦持ち: ヘッダに level と text 列がある (日本語: レベル/階層, 見出し/テキスト も可)
       level,text
       1,はじめに
       2,背景
  B) 横持ち: 列ごとが階層。各行の「空でないセル」が見出し (親の繰り返しは自動で重複排除)
       大見出し,中見出し,小見出し
       はじめに,,
       はじめに,背景,
       はじめに,背景,目的

使い方:
  python csv_to_hierarchy.py hierarchy.csv hierarchy.json
文字コードは UTF-8(BOM可) を試し、失敗したら CP932(Excel既定) で読む。

A/B 形式の正規表現ルール:
  text(横持ちならセル)の先頭に "re:" を付けると、正規表現ルールとして出力する。
       level,text
       2,re:^\d+\.\d+\s
       3,re:^\d+\.\d+\.\d+\s
  (正規表現にカンマを含む場合はセル全体を二重引用符で囲む)
"""

from __future__ import annotations

import csv
import json
import re
import sys

RE_PREFIX = "re:"
NAME_KEYS = {"name", "名前", "ルール名"}
LEVEL_KEYS = {"level", "レベル", "階層", "階層レベル"}
TEXT_KEYS = {"text", "title", "見出し", "テキスト", "タイトル"}
DOC_KEYS = {"document_type", "doc_type", "文書種別"}
REGEX_KEYS = {"regex", "pattern", "正規表現"}
ENABLED_KEYS = {"enabled", "有効"}
PRIORITY_KEYS = {"priority", "優先度"}
PROMOTE_KEYS = {"promote", "見出し化", "昇格"}

_TRUE = {"true", "1", "yes", "y", "on", "有効", "はい", "○", "〇"}
_FALSE = {"false", "0", "no", "n", "off", "無効", "いいえ", "×"}
DEFAULT_PRIORITY = 100


class CsvFormatError(ValueError):
    """CSVの内容・形式が不正なとき。実行時(サーバ内)でも安全に捕捉できるよう ValueError 派生にする。"""


def _print_warning(message: str) -> None:
    print(message, file=sys.stderr)


def read_rows(path: str) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return [row for row in csv.reader(f)]
        except UnicodeDecodeError:
            continue
    raise CsvFormatError(f"文字コードを判定できません(UTF-8/CP932以外): {path}")


def make_entry(text: str, level: int, where: str) -> dict:
    """"re:" 接頭辞なら正規表現ルール、そうでなければ通常の見出しエントリを作る。"""
    if text.lower().startswith(RE_PREFIX):
        pattern = text[len(RE_PREFIX):].strip()
        if not pattern:
            raise CsvFormatError(f"{where}: re: の後に正規表現がありません")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CsvFormatError(f"{where}: 正規表現が不正です: {pattern!r} ({exc})")
        return {"regex": pattern, "level": level}
    return {"text": text, "level": level}


def _col(header: list[str], keys: set[str]) -> int | None:
    return next((i for i, h in enumerate(header) if h in keys), None)


def _cell(row: list[str], idx: int | None) -> str:
    return row[idx].strip() if idx is not None and idx < len(row) else ""


def _parse_bool(value: str, where: str, blank: bool = True) -> bool:
    v = value.strip().lower()
    if v == "":
        return blank
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise CsvFormatError(f"{where}: enabled は true/false で指定してください: {value!r}")


def convert_rules(rows: list[list[str]], header: list[str], warn=_print_warning) -> list[dict]:
    """ルール表形式(name,level,document_type,regex,enabled,priority)を変換する。"""
    cols = {
        "name": _col(header, NAME_KEYS),
        "level": _col(header, LEVEL_KEYS),
        "doc": _col(header, DOC_KEYS),
        "regex": _col(header, REGEX_KEYS),
        "enabled": _col(header, ENABLED_KEYS),
        "priority": _col(header, PRIORITY_KEYS),
        "promote": _col(header, PROMOTE_KEYS),
    }
    out: list[dict] = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        where = f"{n}行目"
        pattern = _cell(row, cols["regex"])
        name = _cell(row, cols["name"])
        if not pattern:
            raise CsvFormatError(f"{where}: regex が空です: {row}")
        try:
            level = int(_cell(row, cols["level"]))
            if level < 1:
                raise ValueError
        except ValueError:
            raise CsvFormatError(f"{where}: level は1以上の整数で指定してください: {row}")
        prio_text = _cell(row, cols["priority"])
        try:
            priority = int(prio_text) if prio_text else DEFAULT_PRIORITY
        except ValueError:
            raise CsvFormatError(f"{where}: priority は整数で指定してください: {prio_text!r}")
        try:
            re.compile(pattern)  # 無効(enabled=false)な行も含め、全行を検証する
        except re.error as exc:
            raise CsvFormatError(f"{where}: 正規表現が不正です: {pattern!r} ({exc})")
        if "\\\\" in pattern:
            warn(
                f"警告 {where} [{name or pattern[:20]}]: バックスラッシュが2つ連続しています。"
                "`\\\\(` は「バックスラッシュ + グループ開始」と解釈されます。"
                "丸括弧そのものを指したい場合は `\\(` に直してください。"
            )
        entry = {
                "name": name,
                "regex": pattern,
                "level": level,
                "document_type": (_cell(row, cols["doc"]) or "all").lower(),
                "enabled": _parse_bool(_cell(row, cols["enabled"]), where),
                "priority": priority,
        }
        if _cell(row, cols["promote"]):  # 空なら省略 = 既定(環境変数)に従う
            entry["promote"] = _parse_bool(_cell(row, cols["promote"]), where)
        out.append(entry)
    return out


def convert(rows: list[list[str]], warn=_print_warning) -> list[dict]:
    if not rows:
        raise CsvFormatError("CSVが空です")
    header = [c.strip().lower() for c in rows[0]]
    body = rows[1:]

    lv_col = _col(header, LEVEL_KEYS)
    tx_col = _col(header, TEXT_KEYS)
    if lv_col is not None and _col(header, REGEX_KEYS) is not None:  # 形式R
        return convert_rules(rows, header, warn)

    out: list[dict] = []
    if lv_col is not None and tx_col is not None:  # 形式A
        for n, row in enumerate(body, start=2):
            if not any(c.strip() for c in row):
                continue
            try:
                level = int(row[lv_col].strip())
                text = row[tx_col].strip()
            except (ValueError, IndexError):
                raise CsvFormatError(f"{n}行目: level/text を読み取れません: {row}")
            if level < 1 or not text:
                raise CsvFormatError(f"{n}行目: levelは1以上、textは必須です: {row}")
            out.append(make_entry(text, level, f"{n}行目"))
        return out

    prev: list[tuple[str, ...]] = []  # 形式B: 深さごとの直前の経路
    for n, row in enumerate(body, start=2):
        cells = [c.strip() for c in row]
        cur: list[tuple[str, ...]] = []
        for i, c in enumerate(cells):
            path = tuple(cells[: i + 1])
            cur.append(path)
            if c and not (len(prev) > i and prev[i] == path):
                out.append(make_entry(c, i + 1, f"{n}行目"))
        prev = cur
    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"使い方: {sys.argv[0]} <input.csv> <output.json>")
    try:
        entries = convert(read_rows(sys.argv[1]))
    except CsvFormatError as exc:
        raise SystemExit(str(exc))
    if not entries:
        raise SystemExit("見出しが1件も得られませんでした")
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"version": 1, "headings": entries}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    disabled = sum(1 for e in entries if e.get("enabled") is False)
    extra = f" (うち無効 {disabled} 件)" if disabled else ""
    print(f"{len(entries)} 件を書き出しました{extra}: {sys.argv[2]}")


if __name__ == "__main__":
    main()

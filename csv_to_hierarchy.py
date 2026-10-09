#!/usr/bin/env python3
r"""階層CSVを階層JSON(custom_hierarchy.py 用)に変換する。

対応するCSV形式:
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

正規表現ルール:
  text(横持ちならセル)の先頭に "re:" を付けると、正規表現ルールとして出力する。
       level,text
       2,re:^\d+\.\d+\s
       3,re:^\d+\.\d+\.\d+\s
  (正規表現にカンマを含む場合はセル全体を二重引用符で囲む)
  正規表現ルールは文書内の見出し文字列に re.search で照合され、一致した見出しに level を適用する。
"""

from __future__ import annotations

import csv
import json
import re
import sys

RE_PREFIX = "re:"
LEVEL_KEYS = {"level", "レベル", "階層", "階層レベル"}
TEXT_KEYS = {"text", "title", "見出し", "テキスト", "タイトル"}


def read_rows(path: str) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return [row for row in csv.reader(f)]
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"文字コードを判定できません(UTF-8/CP932以外): {path}")


def make_entry(text: str, level: int, where: str) -> dict:
    """"re:" 接頭辞なら正規表現ルール、そうでなければ通常の見出しエントリを作る。"""
    if text.lower().startswith(RE_PREFIX):
        pattern = text[len(RE_PREFIX):].strip()
        if not pattern:
            raise SystemExit(f"{where}: re: の後に正規表現がありません")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SystemExit(f"{where}: 正規表現が不正です: {pattern!r} ({exc})")
        return {"regex": pattern, "level": level}
    return {"text": text, "level": level}


def convert(rows: list[list[str]]) -> list[dict]:
    if not rows:
        raise SystemExit("CSVが空です")
    header = [c.strip().lower() for c in rows[0]]
    body = rows[1:]

    lv_col = next((i for i, h in enumerate(header) if h in LEVEL_KEYS), None)
    tx_col = next((i for i, h in enumerate(header) if h in TEXT_KEYS), None)

    out: list[dict] = []
    if lv_col is not None and tx_col is not None:  # 形式A
        for n, row in enumerate(body, start=2):
            if not any(c.strip() for c in row):
                continue
            try:
                level = int(row[lv_col].strip())
                text = row[tx_col].strip()
            except (ValueError, IndexError):
                raise SystemExit(f"{n}行目: level/text を読み取れません: {row}")
            if level < 1 or not text:
                raise SystemExit(f"{n}行目: levelは1以上、textは必須です: {row}")
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
    entries = convert(read_rows(sys.argv[1]))
    if not entries:
        raise SystemExit("見出しが1件も得られませんでした")
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"version": 1, "headings": entries}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{len(entries)} 件の見出しを書き出しました: {sys.argv[2]}")


if __name__ == "__main__":
    main()

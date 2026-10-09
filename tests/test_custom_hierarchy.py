"""custom_hierarchy / csv_to_hierarchy / patch_manager の単体テスト (docling-core のみ必要)。

実行: pytest tests/test_custom_hierarchy.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import csv_to_hierarchy  # noqa: E402
import custom_hierarchy as ch  # noqa: E402


def make_doc(headings):
    """(text, 初期level) の並びから、各見出しの後に本文を持つ文書を作る。"""
    d = DoclingDocument(name="t")
    d.add_title(text="文書タイトル")
    for text, level in headings:
        d.add_heading(text=text, level=level)
        d.add_text(label=DocItemLabel.TEXT, text=f"本文: {text}")
    return d


def md_headings(doc):
    return [ln for ln in doc.export_to_markdown().splitlines() if ln.startswith("#")]


ENTRIES = ch.parse_hierarchy(
    {
        "headings": [
            {"text": "はじめに", "level": 1},
            {"text": "背景", "level": 2},
            {"text": "目的", "level": 2},
            {"text": "方法", "level": 1},
            {"text": "詳細", "level": 2},
            {"text": "詳細", "level": 3},
        ]
    }
)


def test_levels_are_rewritten_to_match_json():
    # docling が全部 level=1 でフラットに出した想定
    doc = make_doc([(e.text, 1) for e in ENTRIES])
    stats = ch.apply_to_document(doc, ENTRIES)
    assert md_headings(doc) == [
        "# 文書タイトル",
        "## はじめに",
        "### 背景",
        "### 目的",
        "## 方法",
        "### 詳細",
        "#### 詳細",  # 同名の見出しは文書順に対応づく
    ]
    assert stats["unmatched_in_document"] == [] and stats["unmatched_in_json"] == []


def test_fuzzy_match_absorbs_numbering_and_width_differences():
    doc = make_doc([("１．はじめに", 1), ("1.1 背景", 1), ("目 的", 1)])
    ch.apply_to_document(doc, ENTRIES[:3])
    assert md_headings(doc)[1:] == ["## １．はじめに", "### 1.1 背景", "### 目 的"]


def test_unmatched_headings_keep_original_level_and_are_reported():
    doc = make_doc([("はじめに", 1), ("全く無関係な見出し", 3)])
    stats = ch.apply_to_document(doc, ENTRIES)
    assert md_headings(doc)[2] == "#### 全く無関係な見出し"
    assert stats["unmatched_in_document"] == ["全く無関係な見出し"]
    assert "背景" in stats["unmatched_in_json"]


def test_level_offset():
    doc = make_doc([("はじめに", 1)])
    ch.apply_to_document(doc, ENTRIES, level_offset=1)
    assert md_headings(doc)[1] == "### はじめに"


def test_nested_and_flat_formats_are_equivalent():
    nested = ch.parse_hierarchy(
        [{"text": "A", "children": [{"text": "B", "children": [{"text": "C"}]}, {"text": "D"}]}]
    )
    assert [(e.text, e.level) for e in nested] == [("A", 1), ("B", 2), ("C", 3), ("D", 2)]
    flat = ch.parse_hierarchy([{"text": "A", "level": 1}, {"text": "B", "level": 2}])
    assert [e.level for e in flat] == [1, 2]


@pytest.mark.parametrize("bad", [[], {"headings": []}, [{"level": 1}], [{"text": "x", "level": 0}]])
def test_invalid_json_is_rejected(bad):
    with pytest.raises(ValueError):
        ch.parse_hierarchy(bad)


def test_apply_hierarchy_uses_json(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps([{"text": "はじめに", "level": 2}]), encoding="utf-8")
    monkeypatch.setenv(ch.ENV_PATH, str(p))
    res = SimpleNamespace(document=make_doc([("はじめに", 1)]))
    ch.apply_hierarchy(res, None)
    assert md_headings(res.document)[1] == "### はじめに"


def test_no_json_or_broken_json_leaves_plain_output(tmp_path, monkeypatch):
    plain = make_doc([("はじめに", 1)]).export_to_markdown()
    # JSONが無い
    monkeypatch.setenv(ch.ENV_PATH, str(tmp_path / "none.json"))
    res = SimpleNamespace(document=make_doc([("はじめに", 1)]))
    ch.apply_hierarchy(res, None)
    assert res.document.export_to_markdown() == plain
    # JSONが壊れている: 例外を出さず素の出力のまま
    p = tmp_path / "h.json"
    p.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv(ch.ENV_PATH, str(p))
    ch._cache.clear()
    ch.apply_hierarchy(res, None)
    assert res.document.export_to_markdown() == plain


def test_mode_auto_ignores_json(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps([{"text": "はじめに", "level": 5}]), encoding="utf-8")
    monkeypatch.setenv(ch.ENV_PATH, str(p))
    monkeypatch.setenv(ch.ENV_MODE, "auto")
    with pytest.raises(ImportError):  # 旧自動推定の経路に入った証拠(本環境では未導入)
        ch.apply_hierarchy(SimpleNamespace(document=make_doc([("はじめに", 1)])), None)


def test_non_pdf_style_document_is_handled():
    """入力形式に依存しない: 文書オブジェクトだけで動く(docx/html等も同じ経路)。"""
    doc = make_doc([("はじめに", 1), ("背景", 1)])
    ch.apply_to_document(doc, ENTRIES)
    assert md_headings(doc)[1:] == ["## はじめに", "### 背景"]


def test_csv_vertical_and_wide_and_cp932(tmp_path):
    v = tmp_path / "v.csv"
    v.write_text("level,text\n1,はじめに\n2,背景\n", encoding="utf-8-sig")
    assert csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(v))) == [
        {"text": "はじめに", "level": 1},
        {"text": "背景", "level": 2},
    ]

    w = tmp_path / "w.csv"
    w.write_bytes("大,中,小\nはじめに,,\nはじめに,背景,\nはじめに,背景,目的\n方法,,\n".encode("cp932"))
    assert csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(w))) == [
        {"text": "はじめに", "level": 1},
        {"text": "背景", "level": 2},
        {"text": "目的", "level": 3},
        {"text": "方法", "level": 1},
    ]


def test_patch_manager_applies_to_original_manager(tmp_path):
    """同梱の manager.py.orig にアンカーが1回だけ一致し、構文エラーなく置換できる。"""
    target = tmp_path / "manager.py"
    shutil.copy(ROOT / "manager.py.orig", target)
    subprocess.run([sys.executable, str(ROOT / "patch_manager.py"), str(target)], check=True)
    text = target.read_text()
    assert "apply_hierarchy(r, s)" in text
    assert "ResultPostprocessor" not in text
    compile(text, str(target), "exec")


# ---- 正規表現ルール ----

def rules(*pairs):
    return ch.parse_hierarchy([{"regex": r, "level": lv} for r, lv in pairs])


NUM_RULES = rules((r"^\d+\.\d+\.\d+\s", 3), (r"^\d+\.\d+\s", 2), (r"^\d+\s", 1))


def test_regex_rules_assign_levels_by_numbering():
    doc = make_doc([("1 はじめに", 2), ("1.1 背景", 1), ("1.1.1 詳細", 1), ("２ 方法", 3)])
    stats = ch.apply_to_document(doc, NUM_RULES)
    assert md_headings(doc)[1:] == ["## 1 はじめに", "### 1.1 背景", "#### 1.1.1 詳細", "## ２ 方法"]
    assert stats["unmatched_in_document"] == []  # 全角数字もNFKCで一致


def test_first_rule_wins_and_unmatched_keeps_level():
    doc = make_doc([("1.1 背景", 1), ("付録", 2)])
    ch.apply_to_document(doc, rules((r"^\d+\.\d+", 2), (r"^\d+", 1)))
    assert md_headings(doc)[1:] == ["### 1.1 背景", "### 付録"]  # 付録は元のlevel=2のまま


def test_text_entries_take_precedence_over_regex_and_regex_does_not_move_cursor():
    entries = ch.parse_hierarchy(
        [
            {"text": "1.1 特別な背景", "level": 3},
            {"regex": r"^\d+\.\d+", "level": 2},
            {"text": "目的", "level": 2},
        ]
    )
    doc = make_doc([("1.1 特別な背景", 1), ("1.2 その他", 1), ("目的", 1)])
    stats = ch.apply_to_document(doc, entries)
    assert md_headings(doc)[1:] == ["#### 1.1 特別な背景", "### 1.2 その他", "### 目的"]
    assert stats["unused_regex_rules"] == [] and stats["unmatched_in_json"] == []


def test_regex_is_case_sensitive_by_default_and_unused_rule_reported():
    doc = make_doc([("CHAPTER 1", 3)])
    stats = ch.apply_to_document(doc, rules((r"^chapter\s*\d+", 1), (r"^付録", 1)))
    assert md_headings(doc)[1] == "#### CHAPTER 1"  # 大文字小文字は区別され、一致しない
    assert stats["unused_regex_rules"] == [r"^chapter\s*\d+", "^付録"]


def test_ignore_case_option():
    entries = ch.parse_hierarchy([{"regex": r"^chapter\s*\d+", "level": 1, "ignore_case": True}])
    doc = make_doc([("CHAPTER 1", 3)])
    ch.apply_to_document(doc, entries)
    assert md_headings(doc)[1] == "## CHAPTER 1"


@pytest.mark.parametrize(
    "bad",
    [
        [{"regex": "(", "level": 1}],  # 不正な正規表現
        [{"regex": "^a"}],  # level必須
        [{"regex": "^a", "text": "a", "level": 1}],  # 同時指定
        [{"regex": "", "level": 1}],
        [{"regex": "^a", "level": 1, "children": [{"text": "b"}]}],
    ],
)
def test_invalid_regex_rules_are_rejected(bad):
    with pytest.raises(ValueError):
        ch.parse_hierarchy(bad)


def test_csv_re_prefix_vertical_and_wide(tmp_path):
    v = tmp_path / "v.csv"
    v.write_text('level,text\n1,はじめに\n2,"re:^\\d+\\.\\d{1,2}\\s"\n', encoding="utf-8-sig")
    out = csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(v)))
    assert out == [{"text": "はじめに", "level": 1}, {"regex": r"^\d+\.\d{1,2}\s", "level": 2}]
    ch.parse_hierarchy(out)  # 変換結果をそのまま読める

    w = tmp_path / "w.csv"
    w.write_text("大,中\n付録,\n付録,re:^表\\d+\n", encoding="utf-8")
    assert csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(w))) == [
        {"text": "付録", "level": 1},
        {"regex": "^表\\d+", "level": 2},
    ]


def test_csv_invalid_regex_raises(tmp_path):
    v = tmp_path / "bad.csv"
    v.write_text("level,text\n1,re:(\n", encoding="utf-8")
    with pytest.raises(csv_to_hierarchy.CsvFormatError):
        csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(v)))


# ---- ルール表CSV (name,level,document_type,regex,enabled,priority) ----

RULES_CSV = ROOT / "examples" / "hierarchy_rules.csv"


def rule_entries(enable=()):
    """サンプルのルール表CSVを読み込む。enable に名前を渡すとそのルールを有効化する。"""
    items = csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(RULES_CSV)))
    for it in items:
        if it["name"] in enable:
            it["enabled"] = True
    return ch.parse_hierarchy(items)


def md_level(doc, text):
    """Markdown 上の見出しレベル(## = 1)。"""
    for line in md_headings(doc):
        hashes = len(line) - len(line.lstrip("#"))
        if line[hashes:].strip() == text:
            return hashes - 1
    raise AssertionError(f"見出しが見つかりません: {text}")


def flat_doc(texts, level=6):
    return make_doc([(t, level) for t in texts])


def test_rule_table_csv_converts_all_rows():
    items = csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(RULES_CSV)))
    assert len(items) == 18
    assert sum(1 for i in items if i["enabled"]) == 5
    first = items[0]
    assert (first["name"], first["level"], first["document_type"], first["priority"]) == (
        "共通_大項目", 1, "all", 100)
    assert {i["document_type"] for i in items} == {"all", "chapter"}
    assert len(ch.parse_hierarchy(items)) == 5  # 無効ルールは読み込み時に除外


def test_user_rules_enabled_set_assigns_levels():
    doc = flat_doc(["1. 総則", "１． 全角の大項目", "(1) 目的", "（１）全角括弧", "ア. 項目", "（ア）細目"])
    stats = ch.apply_to_document(doc, rule_entries())
    assert [md_level(doc, t) for t in ["1. 総則", "１． 全角の大項目"]] == [1, 1]
    assert [md_level(doc, t) for t in ["(1) 目的", "（１）全角括弧"]] == [3, 3]
    assert md_level(doc, "ア. 項目") == 4 and md_level(doc, "（ア）細目") == 5
    assert stats["unmatched_in_document"] == []
    assert stats["rule_hits"]["共通_大項目"] == 2


def test_disabled_rules_do_not_apply():
    doc = flat_doc(["第一章 総則", "① 項目", "A. Intro", "■ 記号"])
    stats = ch.apply_to_document(doc, rule_entries())
    assert [md_level(doc, t) for t in ["第一章 総則", "① 項目", "A. Intro", "■ 記号"]] == [6, 6, 6, 6]
    assert len(stats["unmatched_in_document"]) == 4


def test_document_type_chapter_rules_apply_only_when_requested():
    doc = flat_doc(["1.1 細別項目"])
    ch.apply_to_document(doc, rule_entries())
    assert md_level(doc, "1.1 細別項目") == 6  # chapter ルールは既定では適用されない
    doc = flat_doc(["1.1 細別項目"])
    ch.apply_to_document(doc, rule_entries(), document_types=("chapter",))
    assert md_level(doc, "1.1 細別項目") == 2  # "all" は暗黙に併用される


def test_raw_text_matching_keeps_circled_digits_and_case():
    entries = rule_entries(enable=("丸数字", "英字_大文字", "英字_小文字"))
    doc = flat_doc(["① 項目", "A. Intro", "a. intro"])
    ch.apply_to_document(doc, entries)
    assert md_level(doc, "① 項目") == 3  # NFKC で "1" にされないので丸数字ルールに一致
    assert md_level(doc, "A. Intro") == 3 and md_level(doc, "a. intro") == 4  # 大小を区別


def test_priority_order_and_file_order_on_ties():
    entries = ch.parse_hierarchy([
        {"name": "late", "regex": "^x", "level": 3, "priority": 200},
        {"name": "early", "regex": "^x", "level": 2, "priority": 100},
        {"name": "tie", "regex": "^x", "level": 4, "priority": 100},
    ])
    doc = flat_doc(["x1"])
    stats = ch.apply_to_document(doc, entries)
    assert md_level(doc, "x1") == 2
    assert stats["rule_hits"] == {"early": 1}
    assert set(stats["unused_regex_rules"]) == {"late", "tie"}


def test_disabled_invalid_regex_is_ignored_in_json():
    entries = ch.parse_hierarchy([
        {"regex": "(", "level": 1, "enabled": False},
        {"regex": "^a", "level": 2},
    ])
    assert len(entries) == 1


def test_document_types_env(tmp_path, monkeypatch):
    p = tmp_path / "rules.json"
    items = csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(RULES_CSV)))
    p.write_text(json.dumps({"headings": items}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(ch.ENV_PATH, str(p))
    monkeypatch.setenv(ch.ENV_DOC_TYPES, "all, chapter")
    res = SimpleNamespace(document=flat_doc(["1.1 細別項目"]))
    ch.apply_hierarchy(res, None)
    assert md_level(res.document, "1.1 細別項目") == 2
    monkeypatch.delenv(ch.ENV_DOC_TYPES)
    res = SimpleNamespace(document=flat_doc(["1.1 細別項目"]))
    ch.apply_hierarchy(res, None)
    assert md_level(res.document, "1.1 細別項目") == 6


def test_csv_rule_table_minimal_columns_and_defaults(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("level,regex\n2,^a\n", encoding="utf-8")
    assert csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(p))) == [
        {"name": "", "regex": "^a", "level": 2, "document_type": "all",
         "enabled": True, "priority": 100}
    ]


def test_csv_warns_on_double_backslash(capsys):
    csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(RULES_CSV)))
    err = capsys.readouterr().err
    assert "漢数字_条文" in err and "条文_算用数字" in err and "バックスラッシュ" in err


@pytest.mark.parametrize(
    "csv_text",
    [
        "name,level,regex,enabled\nx,1,^a,maybe\n",  # enabledが不正
        "name,level,regex\nx,0,^a\n",  # levelが0
        "name,level,regex\nx,1,\n",  # regexが空
        "name,level,regex,enabled\nx,1,(,false\n",  # 無効行でも不正な正規表現は検出
        "name,level,regex,priority\nx,1,^a,high\n",  # priorityが不正
    ],
)
def test_csv_rule_table_errors(tmp_path, csv_text):
    p = tmp_path / "e.csv"
    p.write_text(csv_text, encoding="utf-8")
    with pytest.raises(csv_to_hierarchy.CsvFormatError):
        csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(p)))


# ---- CSV直接読み込み + キャッシュ (DOCLING_SERVE_HEADING_RULES_*) ----

import os  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("DOCLING_SERVE_HEADING_RULES_", "HIERARCHY_")):
            monkeypatch.delenv(k)
    ch.clear_caches()
    yield monkeypatch
    ch.clear_caches()


def _run(texts=("1. 総則", "本文です")):
    doc = flat_doc(list(texts))
    ch.apply_hierarchy(SimpleNamespace(document=doc), None)
    return doc


def test_env_csv_applies_rules(env):
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "true")
    env.setenv("DOCLING_SERVE_HEADING_RULES_CSV", str(RULES_CSV))
    assert md_level(_run(), "1. 総則") == 1


def test_env_disabled_or_unset_is_plain_output(env):
    env.setenv("DOCLING_SERVE_HEADING_RULES_CSV", str(RULES_CSV))  # ENABLED なし
    assert md_headings(_run()) == md_headings(flat_doc(["1. 総則", "本文です"]))
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "false")
    assert md_headings(_run()) == md_headings(flat_doc(["1. 総則", "本文です"]))


def test_env_enabled_without_csv_is_noop(env):
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "true")
    assert md_headings(_run()) == md_headings(flat_doc(["1. 総則", "本文です"]))


def test_csv_is_cached_and_reloaded_on_change(env, tmp_path, monkeypatch):
    p = tmp_path / "r.csv"
    p.write_text("name,level,regex\na,1,^A\n", encoding="utf-8")
    calls = []
    orig = csv_to_hierarchy.convert
    monkeypatch.setattr(csv_to_hierarchy, "convert",
                        lambda *a, **k: calls.append(1) or orig(*a, **k))
    first = ch.load_rules_csv(str(p))
    assert ch.load_rules_csv(str(p)) is first and len(calls) == 1  # 2回目は再変換しない
    p.write_text("name,level,regex\na,2,^A\nb,1,^B\n", encoding="utf-8")
    again = ch.load_rules_csv(str(p))
    assert len(calls) == 2 and len(again) == 2  # 更新を検知して再変換


def test_bad_csv_returns_plain_and_does_not_reparse(env, tmp_path, monkeypatch):
    p = tmp_path / "bad.csv"
    p.write_text("name,level,regex\nx,1,(\n", encoding="utf-8")
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "true")
    env.setenv("DOCLING_SERVE_HEADING_RULES_CSV", str(p))
    calls = []
    orig = csv_to_hierarchy.convert
    monkeypatch.setattr(csv_to_hierarchy, "convert",
                        lambda *a, **k: calls.append(1) or orig(*a, **k))
    assert md_headings(_run()) == md_headings(flat_doc(["1. 総則", "本文です"]))
    _run()
    assert len(calls) == 1  # 失敗も同一内容なら再解析しない
    p.write_text("name,level,regex\nx,1,^1\\.\n", encoding="utf-8")  # 修正すると復旧
    assert md_level(_run(), "1. 総則") == 1


def test_missing_csv_is_plain_and_recovers(env, tmp_path):
    p = tmp_path / "later.csv"
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "true")
    env.setenv("DOCLING_SERVE_HEADING_RULES_CSV", str(p))
    assert md_headings(_run()) == md_headings(flat_doc(["1. 総則", "本文です"]))
    p.write_text("name,level,regex\nx,2,^1\\.\n", encoding="utf-8")
    assert md_level(_run(), "1. 総則") == 2


def test_env_document_types_and_new_names(env):
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "1")
    env.setenv("DOCLING_SERVE_HEADING_RULES_CSV", str(RULES_CSV))
    env.setenv("DOCLING_SERVE_HEADING_RULES_DOCUMENT_TYPES", "all,chapter")
    assert md_level(_run(("1.1 背景",)), "1.1 背景") == 2
    env.setenv("DOCLING_SERVE_HEADING_RULES_DOCUMENT_TYPES", "all")
    doc = _run(("1.1 背景",))
    assert md_headings(doc) == md_headings(flat_doc(["1.1 背景"]))


# ---- promote: 見出しと判定されなかった本文の昇格 ----

Q_ROWS = (
    "name,level,document_type,regex,enabled,priority,promote\n"
    'Q番号,3,all,"^[QＱ][0-9０-９]+(?:[.．:：].*|[ 　\\t]+.*)?$",true,100,true\n'
    'Q枝番,4,all,"^[QＱ][0-9０-９]+[-－ー‐−][0-9０-９]+(?:[.．:：].*|[ 　\\t]+.*)?$",true,100,true\n'
    'ノーpromote,2,all,"^[0-9]+\\\\.\\\\s.+$",true,100,false\n'
)


def _promote_doc():
    from docling_core.types.doc import DocItemLabel
    doc = DoclingDocument(name="t")
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="アンケート", level=1)
    for t in ["Q1. 業務でAIを利用したことがありますか？",
              "ない（Q1-1,Q1-2で回答終了）　　ある（Q2へ）",
              "Q1-1. 利用しなかった理由", "Q2-4. 具体的な利用方法", "1. 本文の番号です"]:
        doc.add_text(label=DocItemLabel.TEXT, text=t)
    return doc


def test_promote_text_to_heading(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text(Q_ROWS, encoding="utf-8")
    entries = ch.parse_hierarchy(csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(p))))
    doc = _promote_doc()
    stats = ch.apply_to_document(doc, entries)
    md = doc.export_to_markdown()
    assert "#### Q1. 業務でAIを利用したことがありますか？" in md  # level3 -> ####
    assert "##### Q1-1. 利用しなかった理由" in md and "##### Q2-4. 具体的な利用方法" in md
    assert "\nない（Q1-1,Q1-2で回答終了）　　ある（Q2へ）" in md and "#" not in md.split("ない（")[1].split("\n")[0]
    assert "\n1. 本文の番号です" in md and "# 1. 本文" not in md  # promote=false は本文のまま
    assert stats["promoted"] == 3


def test_promote_false_keeps_plain_output(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text(Q_ROWS.replace(",true\n", ",false\n"), encoding="utf-8")
    entries = ch.parse_hierarchy(csv_to_hierarchy.convert(csv_to_hierarchy.read_rows(str(p))))
    doc = _promote_doc()
    before = _promote_doc().export_to_markdown()
    ch.apply_to_document(doc, entries)
    assert doc.export_to_markdown() == before


def test_promote_max_chars_and_json_roundtrip(tmp_path):
    entries = ch.parse_hierarchy([{"regex": "^Q1", "level": 2, "promote": True}])
    doc = _promote_doc()
    ch.apply_to_document(doc, entries, promote_max_chars=5)
    assert "# Q1" not in doc.export_to_markdown()  # 長すぎる行は昇格しない
    doc2 = _promote_doc()
    ch.apply_to_document(doc2, entries)
    assert "### Q1. 業務で" in doc2.export_to_markdown()
    assert DoclingDocument.model_validate(doc2.model_dump()).export_to_markdown() == doc2.export_to_markdown()


def test_promote_default_on_and_env_default_off(tmp_path):
    entries = ch.parse_hierarchy([{"regex": r"^Q\d+\.", "level": 2}])  # promote 未指定
    doc = _promote_doc()
    assert ch.apply_to_document(doc, entries)["promoted"] == 1  # 既定で昇格する
    doc2 = _promote_doc()
    assert ch.apply_to_document(doc2, entries, promote_default=False)["promoted"] == 0
    # promote=false の明示は既定より優先
    e2 = ch.parse_hierarchy([{"regex": r"^Q\d+\.", "level": 2, "promote": False}])
    assert ch.apply_to_document(_promote_doc(), e2, promote_default=True)["promoted"] == 0


def test_promote_env_switch(env):
    env.setenv("DOCLING_SERVE_HEADING_RULES_ENABLED", "true")
    env.setenv("DOCLING_SERVE_HEADING_RULES_CSV", str(ROOT / "examples" / "hierarchy_rules_promote.csv"))
    doc = _promote_doc()
    ch.apply_hierarchy(SimpleNamespace(document=doc), None)
    assert "#### Q1. 業務で" in doc.export_to_markdown()
    ch.clear_caches()
    env.setenv("DOCLING_SERVE_HEADING_RULES_PROMOTE", "false")
    doc = _promote_doc()
    ch.apply_hierarchy(SimpleNamespace(document=doc), None)
    assert "#" not in doc.export_to_markdown().split("アンケート")[1]

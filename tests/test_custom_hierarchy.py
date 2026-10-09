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
        d.add_text(label=DocItemLabel.TEXT, text=f"{text}の本文")
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


def test_apply_hierarchy_uses_json_and_falls_back(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps([{"text": "はじめに", "level": 2}]), encoding="utf-8")
    monkeypatch.setenv(ch.ENV_PATH, str(p))
    res = SimpleNamespace(document=make_doc([("はじめに", 1)]))
    ch.apply_hierarchy(res, None)
    assert md_headings(res.document)[1] == "### はじめに"

    # 壊れたJSON → 例外を出さず自動推定側(ここでは未導入なのでImportError)に落ちる
    p.write_text("{broken", encoding="utf-8")
    ch._cache.clear()
    with pytest.raises(ImportError):
        ch.apply_hierarchy(res, None)


def test_mode_auto_ignores_json(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps([{"text": "はじめに", "level": 5}]), encoding="utf-8")
    monkeypatch.setenv(ch.ENV_PATH, str(p))
    monkeypatch.setenv(ch.ENV_MODE, "auto")
    with pytest.raises(ImportError):  # 自動推定の経路に入った証拠
        ch.apply_hierarchy(SimpleNamespace(document=make_doc([("はじめに", 1)])), None)


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

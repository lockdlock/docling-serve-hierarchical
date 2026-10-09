# docling-serve-hierarchical

Downstream images that extend the official [docling-serve](https://github.com/docling-project/docling-serve) images with automatic PDF header-hierarchy inference via [docling-hierarchical-pdf](https://github.com/krrome/docling-hierarchical-pdf).

## What's different

Every conversion result produced by docling is post-processed by `ResultPostprocessor` before it is exported. This restructures section headings in the `DoclingDocument` to reflect the true heading hierarchy inferred from the PDF's table of contents or font metrics.

The only files modified relative to upstream are:

| File | Change |
|------|--------|
| `docling_serve/convert/manager.py` | Patches the result with docling-hierarchical-pdf ResultPostprocessor |

`docling-hierarchical-pdf` is installed as an additional package on top of the upstream environment.

## Images

Published to GHCR under `ghcr.io/krrome/`:

| Image | Based on | Models pre-installed |
|-------|----------|----------------------|
| `docling-serve-hierarchical` | `docling-project/docling-serve` | No |
| `docling-serve-hierarchical-cpu` | `docling-project/docling-serve-cpu` | No |
| `docling-serve-hierarchical-cu128` | `docling-project/docling-serve-cu128` | No |
| `docling-serve-hierarchical-cu130` | `docling-project/docling-serve-cu130` | No |
| `docling-serve-hierarchical-models` | `docling-project/docling-serve` | Yes |
| `docling-serve-hierarchical-cpu-models` | `docling-project/docling-serve-cpu` | Yes |
| `docling-serve-hierarchical-cu128-models` | `docling-project/docling-serve-cu128` | Yes |
| `docling-serve-hierarchical-cu130-models` | `docling-project/docling-serve-cu130` | Yes |

Tags mirror the upstream commit SHA (`sha-<hash>`) used at build time. The default/cpu variants are also tagged `latest`.

## Licenses

The software in these images is MIT-licensed ([docling-serve](https://github.com/docling-project/docling-serve/blob/main/LICENSE), [docling](https://github.com/docling-project/docling/blob/main/LICENSE)) and Apache 2.0-licensed (EasyOCR). The base layer is Red Hat UBI, which [explicitly permits redistribution](https://www.redhat.com/licenses/EULA_Red_Hat_Universal_Base_Image_English_20190422.pdf).

The `-models` images additionally contain model weights from [`ds4sd/docling-models`](https://huggingface.co/ds4sd/docling-models) (layout analysis and TableFormer), which are released under [CDLA Permissive 2.0](https://cdla.dev/permissive-2-0/) and [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0). Both licenses permit redistribution; if you redistribute a `-models` image you must pass these license terms on to your recipients.

## 独自の階層JSONで見出し階層を指定する (custom_hierarchy)

素の docling-serve が出力した文書(PDFに限らず docx / pptx / html / 画像OCR など)に対し、
自分で作った階層(CSV→JSON)を見出し階層として後段で適用できます。md / json / text すべての出力形式に反映されます。
**JSONが無い場合は素の docling-serve と同じ出力**です(旧来のPDF向け自動推定は `HIERARCHY_MODE=auto` で明示した時のみ)。
JSONは**全PDF共通の1ファイル**で、PDF内の見出しテキストと突き合わせてレベルを付け直します
(全半角・空白・句読点・章番号の有無の差は吸収。同名見出しは文書順に対応付け)。

### 1. CSVをJSONに変換

```bash
python csv_to_hierarchy.py examples/hierarchy.csv hierarchy.json
```

CSV形式は2種類(文字コードはUTF-8/CP932を自動判定):

- 縦持ち: `level,text` 列 (例: `examples/hierarchy.csv`)
- 横持ち: 列ごとが階層 (`大見出し,中見出し,小見出し`)。親の繰り返し行は自動で重複排除

#### ルール表CSV (name,level,document_type,regex,enabled,priority)

ヘッダに `regex` と `level` がある場合は、ルール表として読み込みます(例: `examples/hierarchy_rules.csv`)。

| 列 | 内容 |
| -- | ---- |
| name | ルール名(ログ表示用) |
| level | 見出しレベル(1以上) |
| document_type | 適用する文書種別。空なら `all` |
| regex | 正規表現(見出し文字列に `re.search`、大文字小文字は区別) |
| enabled | `true`/`false`(`1`/`0`、`有効`/`無効`、`○`/`×` も可。空は true) |
| priority | **小さいほど先に評価**(空なら100)。同値はCSVの順 |

- `enabled=false` の行もJSONに出力されますが、読み込み時に無視されます(正規表現の誤りはCSV変換時に全行検査)。
- `document_type=all` のルールは常に適用されます。それ以外(例: `chapter`)は、環境変数
  `HIERARCHY_DOCUMENT_TYPES=all,chapter` に含めた場合だけ適用されます(既定は `all` のみ)。
- 変換時、`\\(` のようにバックスラッシュが2つ連続する正規表現には警告を出します
  (`\(` は「バックスラッシュ + グループ開始」と解釈されるため。丸括弧そのものは `\(`)。
- 適用状況はログ(INFO)の `rule_hits` で確認できます。

#### 正規表現で指定する

章番号などのパターンで階層を決めたい場合は、`text` の代わりに `regex` を使います
(CSVでは `text` の先頭に `re:` を付けます。例: `examples/hierarchy_regex.csv`)。

```json
{"headings": [
  {"regex": "^\\d+\\.\\d+\\.\\d+\\s", "level": 3},
  {"regex": "^\\d+\\.\\d+\\s",        "level": 2},
  {"regex": "^\\d+\\s",               "level": 1},
  {"text": "付録", "level": 1}
]}
```

- 見出し文字列を**加工せず**(前後の空白除去のみ)`re.search` で照合します。大文字小文字は**区別します**
  (区別しない場合は `"ignore_case": true`)。全角/半角はパターン側で両方書いてください(例: `[0-9０-９]`)。
- `level` は必須。複数のルールに一致する場合は**ファイル内で先に書いたもの**が優先です
  (上の例のように、細かい階層=長いパターンを先に書いてください)。
- `text` エントリで照合できた見出しが優先され、`text` で見つからなかった見出しにだけ正規表現を適用します。
- どちらにも一致しない見出しは元のレベルのままです。一度も使われなかったルールはログ(INFO)に出ます。
- 不正な正規表現を含むJSONは全体が無効扱いとなり、素のdocling出力のまま返します(ログにERROR)。
- CSVでカンマを含む正規表現は、セル全体を二重引用符で囲んでください。

### 2. コンテナにJSONを渡す

```bash
docker run ... \
  -v /opt/hierarchy:/opt/hierarchy:ro \   # /opt/hierarchy/hierarchy.json を置く
  docling-serve-hierarchical:custom
```

- JSONを差し替えるだけで次の変換から反映(再起動不要)
- パスを変える場合は `HIERARCHY_JSON_PATH`
- 一致しきい値 `HIERARCHY_MATCH_THRESHOLD` (既定 0.85)、レベル加算 `HIERARCHY_LEVEL_OFFSET` (既定 0)

### 見出しレベルの対応

doclingの仕様で、タイトルは `#`、JSONの `level=1` は `##`、`level=2` は `###` になります。
見出しレベルの下限は1(`##`)のため、`#` は文書タイトル専用です。`HIERARCHY_LEVEL_OFFSET` で全体を深く(正の値)できますが、
`#` にはできません。

### ログ

変換ごとに、JSONに無い見出し(元のレベルのまま)と、文書内で見つからなかったJSON見出しをWARNINGで出力します。
マッチ漏れの調整に使えます。

### 元に戻す手順 (ロールバック)

| 目的 | 手順 |
| ---- | ---- |
| 素のdocling出力に戻す | `/opt/hierarchy/hierarchy.json` のマウントを外す(ファイルが無ければ何もしない) |
| 旧来のPDF向け自動推定にする | コンテナに `HIERARCHY_MODE=auto` を付けて再作成 (JSONは無視される) |
| 改修前のイメージに戻す | `docker run` / compose の image を改修前のタグ(`ghcr.io/krrome/docling-serve-hierarchical:<以前のsha>`)に戻して再作成 |
| ソースを戻す | `git revert <この改修のコミット>` で `patch_manager.py` / `Containerfile` が元に戻る |

JSONが壊れている・適用中に例外が出た場合も、変換自体は止めず素のdocling出力のまま返します(ログにERROR)。

### テスト

```bash
pip install docling-core pytest
pytest tests/test_custom_hierarchy.py
```

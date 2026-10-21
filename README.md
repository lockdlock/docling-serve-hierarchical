# docling-serve-hierarchical

> **Acknowledgements**: This is a fork of [Roman Kayan's (@krrome) original project](https://github.com/krrome/docling-serve-hierarchical) (MIT License). Many thanks to the original author.

公式の [docling-serve](https://github.com/docling-project/docling-serve) に、**変換後の見出し階層(Markdown の `#` の深さ)を、ルール表CSVで付け直す機能**を追加します。
[krrome/docling-serve-hierarchical](https://github.com/krrome/docling-serve-hierarchical) のフォークです。

- PDF / docx / pptx / html など、docling が出力した文書に、後段で適用します(md / json / html / text すべてに反映)。
- 設定が無い場合は、**素の docling-serve と同じ出力**です。
- Word の**自動番号**(`1. 目的` など)や、見出しスタイルのない `Q1. …` も、ルールに一致すれば見出しにできます。
- CSV を編集するだけで次の変換から反映されます(再起動・再ビルド不要)。

## インストール

前提: docling-serve を Docker Compose で動かしており、`compose.yml` / `Dockerfile` / `.env` がある。
以下は `/opt/docker/docling-serve` にそれらがあるものとして説明します(読み替えてください)。Docker Compose v2.17 以降が必要です(`docker compose version`)。

**流れ: クローン → `.env` / `Dockerfile` / `compose.yml` に追記 → ビルド → 起動**

### 0. バックアップ(元に戻すため・推奨)

```bash
cd /opt/docker/docling-serve
mkdir -p backup-before-hierarchy
cp -a compose.yml Dockerfile .env backup-before-hierarchy/
IMG=$(docker compose config --images)
echo "$IMG"                                 # 名前:タグ が表示されること
docker tag "$IMG" "${IMG}-before-hierarchy"
```

### 1. クローン

docling-serve のディレクトリと**同じ階層**にクローンします。

```bash
cd /opt/docker
git clone https://github.com/lockdlock/docling-serve-hierarchical.git
chmod 755 docling-serve-hierarchical/config
chmod 644 docling-serve-hierarchical/config/heading_rules.csv
```

`docling-serve-hierarchical/config/heading_rules.csv` が**ルール表**です。編集はこのファイルで行います。

### 2. `.env` に追記

`/opt/docker/docling-serve/.env` に、次の2行を追加します(すでにあれば追記されません)。

```bash
cd /opt/docker/docling-serve
grep -q HEADING_RULES .env || printf '\n# CSV-based heading rules (custom feature)\nDOCLING_SERVE_HEADING_RULES_ENABLED=true\nDOCLING_SERVE_HEADING_RULES_CSV=/config/heading_rules.csv\n' >> .env
```

- `DOCLING_SERVE_HEADING_RULES_CSV` は、ホストではなく**コンテナ内のパス**です(手順4でマウントします)。
- 行末にコメントを書かないでください。コメントは別の行に書きます。

### 3. `Dockerfile` の末尾に追記

```dockerfile
# --- 見出し階層(CSV): クローンを読み取り専用でマウントして適用 ---
RUN --mount=type=bind,from=hierarchy,target=/mnt/hierarchy \
    D=$(python -c "import docling_jobkit.convert as c; print(c.__path__[0])") && \
    cp /mnt/hierarchy/custom_hierarchy.py /mnt/hierarchy/csv_to_hierarchy.py "$D/" && \
    python /mnt/hierarchy/patch_manager.py "$D/manager.py"
```

- `USER 0` を使う層(LibreOffice のインストールなど)がある場合は、`USER 1001` に戻した**後**に追記します。
- root 権限・`/tmp` への書き込みは不要です。
- 上流の `manager.py` が変わってパッチ位置が見つからない場合は、**ビルドが失敗します**(壊れたイメージはできません)。

### 4. `compose.yml` に追記(該当サービス内)

```yaml
    build:
      additional_contexts:
        hierarchy: /opt/docker/docling-serve-hierarchical   # build: の下に追加
    env_file:
      - .env                                                # 無ければ追加
    volumes:
      - /opt/docker/docling-serve-hierarchical/config:/config:ro   # 追加
```

- `additional_contexts` の `hierarchy` は、Dockerfile の `from=hierarchy` と**同じ名前**にします。
- `.env` は `env_file` を指定しないと、コンテナに渡りません(Compose が自動で読むのは、`compose.yml` 内の `${変数}` の置換用です)。
- **ファイル単体ではなく、`config` ディレクトリをマウント**します。エディタや `scp` が保存時にファイルを置き換えると、ファイル単体のマウントには更新が反映されないためです。

### 5. ビルドと起動

```bash
cd /opt/docker/docling-serve
docker compose config | grep -E 'hierarchy|HEADING_RULES|/config'   # 反映の確認
docker compose build 2>&1 | tee /tmp/build.log | tail -30
docker compose up -d
```

`docker compose build` が失敗した場合は、`up -d` に進まないでください(動いているコンテナはそのままです)。
ビルドログに `Patched ... manager.py successfully.` が出れば適用されています。

### 6. 確認

```bash
docker compose exec docling-serve env | grep HEADING_RULES            # 2行出ること
docker compose exec docling-serve ls -l /config/heading_rules.csv     # CSV が見えること
# 何か1つ変換したあとで:
docker compose logs --since 10m docling-serve | grep "hierarchy applied"
```

`hierarchy applied: changed=… promoted=… rule_hits={…}` が出れば動作しています(`rule_hits` が空なら、どのルールにも一致していません)。

### 元に戻す

```bash
cd /opt/docker/docling-serve
cp -a backup-before-hierarchy/compose.yml backup-before-hierarchy/Dockerfile backup-before-hierarchy/.env .
IMG=$(docker compose config --images)
docker tag "${IMG}-before-hierarchy" "$IMG"
docker compose up -d --no-build
```

機能だけを一時的に止める場合は、`.env` の `DOCLING_SERVE_HEADING_RULES_ENABLED=false` にして `docker compose up -d` を実行します。

### 更新

```bash
cd /opt/docker/docling-serve-hierarchical && git pull --ff-only
cd /opt/docker/docling-serve && docker compose build && docker compose up -d
```

`config/heading_rules.csv` を編集して使う場合は、`git pull` との衝突を避けるため、一度だけ次を実行します。

```bash
cd /opt/docker/docling-serve-hierarchical
git update-index --skip-worktree config/heading_rules.csv
```

`git clean -fdx` は使わないでください(未追跡のファイルが消えます)。

## PDFの自動推定と併用する(任意)

PDF の目次・番号・フォントから見出し階層を**自動で推定**し、その結果を**CSVルールで校正**できます。CSV だけで足りる場合は、この手順は不要です。

### 推奨: docling 本体の推定(`do_pdf_heading_hierarchy`)

最近の docling / docling-serve には、見出し階層の推定機能が組み込まれています。**追加のインストールは不要**です。変換リクエストで `do_pdf_heading_hierarchy=true` を指定するだけで、次の順に処理されます。

1. docling が PDF を変換し、見出しレベルを推定する(`do_pdf_heading_hierarchy=true` のとき。**PDF・画像のみ**)
2. このフォークがCSVルールを適用する(`DOCLING_SERVE_HEADING_RULES_ENABLED=true` のとき)
3. Markdown などに出力する

- 対応状況の確認: `curl -s http://localhost:5001/openapi.json | grep -o do_pdf_heading_hierarchy`(表示されれば対応済み)
- [paperless-docling](https://github.com/lockdlock/paperless-docling) では `PAPERLESS_DOCLING_DO_PDF_HEADING_HIERARCHY=true` がこのオプションに対応します。
- docx など PDF 以外は、推定の対象外です(CSV だけが効きます)。

> **ポイント**
> - `do_pdf_heading_hierarchy` は**リクエストごとのオプション**で、**呼び出し側が送る必要があります**。サーバー(`.env` など)で設定するものではありません。
> - 指定しない(`false`)場合、docling は PDF の見出しを**すべて同じレベル(level 1 = `##`)**で出力します。CSV に一致した見出しだけが、ルールのレベルに直ります。
> - 推定は、**PDF のしおり(目次) → 番号(`1.` → `1.1`) → フォント(大きさ・太さ)** の順に手がかりを使います。しおりのある PDF が最も確実です。
> - 推定の結果を、CSV が**上書き**します(レベルは絶対値で設定されます)。CSV に一致しない見出しは、推定の結果のままです。

**メリット**

- 追加のインストールや再ビルドが不要で、イメージが大きくなりません。
- リクエストごとに ON / OFF できます(文書の種類に応じた使い分けができます)。
- docling 本体の機能なので、docling のバージョンアップに合わせて改善されます。

**デメリット・注意点**

- オプションを送れない連携先では使えません(サーバー側で一括して有効にはできません。その場合は、下記の `HIERARCHY_MODE=auto` が代替になります)。
- PDF・画像のみが対象です(docx などは対象外)。
- 古い docling / docling-serve には、このオプションがありません(上の確認コマンドで調べます)。
- しおりも番号も無い PDF は、フォントだけが手がかりになり、誤ることがあります。フォントによる推定は、docling 側の設定によっては働かない場合があります。
- 推定の精度は文書に依存します。誤りやすい見出しは、CSV のルールで補ってください。

**CSV は見出しレベルを「絶対値」で設定します**(推定の結果に加算されません)。

| 状況 | 結果 |
|------|------|
| 推定が `###` にした見出しが、CSV では level=1 に一致 | `##` に直る(`#####` のように深くはならない) |
| 推定が `##` にした見出しが、CSV では level=2 に一致 | `###` に直る |
| どのルールにも一致しない見出し | 推定の結果のまま |
| 文書タイトル(`#`) | 変更しない(`#` は文書タイトル専用。CSV の level=1 は `##` になる) |

同じCSVを何度適用しても結果は変わりません。

### 代替: `HIERARCHY_MODE=auto`(docling-hierarchical-pdf)

> **注意: 基本は docling 内蔵の `do_pdf_heading_hierarchy` を使ってください。** こちらは追加のパッケージ(scikit-learn など)が必要で、通常は不要です。

使うのは、次のような場合に限ります。

- リクエストにオプション(`do_pdf_heading_hierarchy`)を付けられない連携先を、**サーバー側で一括して**推定したい場合
- docling 本体が古く、上記のオプションが無い場合

**本体の推定とは同時に有効にしないでください**(推定が二重になります)。

1. Docker Serve でビルドする `Dockerfile` の、見出し階層のブロック(`RUN --mount=…`)の**前**に1行追加します。

```dockerfile
RUN pip install --no-cache-dir docling-hierarchical-pdf==0.2.0
```

2. `.env` に `HIERARCHY_MODE=auto` を追加し、再ビルドして起動します。

```bash
cd /opt/docker/docling-serve
grep -q '^HIERARCHY_MODE=' .env || printf 'HIERARCHY_MODE=auto\n' >> .env
docker compose build 2>&1 | tee /tmp/build.log | tail -20
docker compose up -d
```

- `HIERARCHY_MODE` で有効な値は `auto` のみです。それ以外の値・未設定は無効です。
- 推定は PDF のみで、失敗しても変換は止まりません(ログに記録され、CSV だけが適用されます)。
- 元に戻す: `.env` の `HIERARCHY_MODE=auto` の行を削除して `docker compose up -d`(再ビルド不要)。

## ルール表CSV

置き場所: `docling-serve-hierarchical/config/heading_rules.csv`(コンテナ内の `/config/heading_rules.csv`)。
サンプル: [`config/heading_rules.csv`](config/heading_rules.csv)([`examples/heading_rules.csv`](examples/heading_rules.csv) と同じ内容)。

| 列 | 内容 |
|----|------|
| name | ルール名(ログ表示用) |
| level | 見出しレベル(1以上) |
| document_type | 適用する文書種別。空なら `all` |
| regex | 正規表現(見出し文字列に `re.search`。大文字小文字は区別) |
| enabled | `true` / `false`(`1`/`0`、`有効`/`無効`、`○`/`×` も可。空は true) |
| priority | **小さいほど先に評価**(空は100)。同値はCSVの順 |
| promote | (任意)`false` でそのルールは本文・リスト項目を見出しに昇格しない |

- 文字コードは UTF-8(BOM可)または CP932(Excel既定)を自動判定します。
- サンプルは20ルール(有効7、無効13)です。使いたいルールの `enabled` を `true` にしてください。
- `document_type=chapter` のルール(`章型_細別項目`)は、`DOCLING_SERVE_HEADING_RULES_DOCUMENT_TYPES=all,chapter` を指定したときだけ適用されます。
- カッコそのものを指す正規表現は、`\(` と書きます(`\\(` は別の意味になるため警告が出ます)。
- 見出しのレベルは、docling の仕様で `level=1` が `##`、`level=2` が `###` になります(`#` は文書タイトル専用)。
- CSV が不正・読めない場合は、素の docling 出力のまま返し、ログに ERROR を1回出します。CSV を直すと次の変換で復旧します。
- CSV の更新(更新時刻・サイズの変化)は自動で検知されます。

### 見出しにならなかった行を、見出しに昇格する(標準で有効)

docx などで見出しスタイルが使われていない文書でも、ルールの正規表現に一致した行は、見出しに昇格します。

| 元の行 | 扱い |
|--------|------|
| 本文(段落) `1. 目的`、`Q1. …` | 昇格する |
| **Word の自動番号(リスト項目)** | 番号(`1.`)と本文を結合した `1. 目的` で照合し、昇格する。見出しの文字列は `1. 目的` になる |
| 文末が `。` `！` `？` `!` `?` のリスト項目 | 文とみなし、昇格しない(箇条書きのまま) |
| 記号の箇条書き(`-` `・`)、コード、表 | 対象外 |

- 昇格する行は、最大100文字までです。
- 注意: `1. …` のように広く一致するルールは、同じ形の行を見出しにします。誤昇格する場合は、そのルールの `promote` を `false` にするか、`enabled` を `false` にするか、正規表現を絞ってください。

## 環境変数

| 区分 | 変数 | 内容 | 既定 |
|------|------|------|------|
| 必須 | `DOCLING_SERVE_HEADING_RULES_ENABLED` | `true`(`1`/`yes`/`on`)で有効。それ以外は素の docling 出力 | 無効 |
| 必須 | `DOCLING_SERVE_HEADING_RULES_CSV` | CSV の**コンテナ内**パス | なし |
| 任意 | `HIERARCHY_MODE` | **指定できる値は `auto` のみ**。`auto` で PDF の自動推定を先に行い、その後に CSV で校正する(上記「PDFの自動推定と併用する」)。未設定・それ以外の値(`off` など、綴りの誤りを含む)は、すべて無効として扱われる | 無効 |
| 任意 | `DOCLING_SERVE_HEADING_RULES_DOCUMENT_TYPES` | 適用する `document_type`(カンマ区切り) | `all` |
| 任意 | `DOCLING_SERVE_HEADING_RULES_MATCH_THRESHOLD` | `text` エントリの一致しきい値(0〜1) | `0.85` |
| 任意 | `DOCLING_SERVE_HEADING_RULES_LEVEL_OFFSET` | 見出しレベルに加算する値 | `0` |
| 任意 | `DOCLING_SERVE_HEADING_RULES_PROMOTE` | `false` で、見出しへの昇格を全体で止める | `true` |
| 任意 | `DOCLING_SERVE_HEADING_RULES_PROMOTE_MAX_CHARS` | 昇格する行の最大文字数 | `100` |
| 任意 | `DOCLING_SERVE_HEADING_RULES_PROMOTE_LIST_ITEMS` | `false` で、自動番号(リスト項目)の昇格だけを止める | `true` |

変更したら、`docker compose up -d` でコンテナを作り直します(再ビルドは不要です)。

## トラブルシューティング

| 症状 | 原因と対処 |
|------|-----------|
| ビルドで `docker.io/library/hierarchy:latest` の pull に失敗 | `compose.yml` の `build:` に `additional_contexts: hierarchy: …` が無い。手順4を確認し、`docker compose config \| grep hierarchy:` で表示されること |
| ビルドが `anchor expected exactly once` で失敗 | 上流の `manager.py` が変わった。`patch_manager.py` の `ANCHOR` の更新が必要(古いバージョンのベースイメージに固定すれば回避できる) |
| コンテナに環境変数が入らない | `compose.yml` に `env_file: [.env]` が無い |
| `/config` がディレクトリ/空になる | ホスト側の `config/heading_rules.csv` が無いまま起動した。ファイルを置いてから `up -d` |
| 見出しにならない(`hierarchy applied` の `promoted=0`) | 行が自動番号(リスト項目)で、古いバージョンを使っている。`git pull` して再ビルド。または、正規表現が一致していない(`rule_hits` を確認) |
| `git pull` が「untracked working tree files」で失敗 | 手元の `config/heading_rules.csv` を一時退避してから `pull` し、戻す(上記「更新」参照) |
| 「No page images in this result」が出る | エラーではない。画像出力の設定(画像を含める・`placeholder` 以外・zip)をしていないという通知 |

## テスト

```bash
pip install docling-core pytest
pytest tests/test_custom_hierarchy.py
```

`docling-core` のバージョン差で、一部のテストの結果が変わることがあります(本番と同じ結果は、ビルドしたイメージ内で確認してください)。

## 旧来の方式(参考)

- **階層JSON方式**: `hierarchy.json`(`HIERARCHY_JSON_PATH`)で、見出しテキストまたは正規表現とレベルを指定する従来方式です。`csv_to_hierarchy.py` で CSV から作れます。CSV方式(`ENABLED=true`)が有効なときは、CSVが優先されます。サンプル: `examples/hierarchy*.csv`。
- **PDF向け自動推定**: docling 本体の `do_pdf_heading_hierarchy`(推奨)、または `HIERARCHY_MODE=auto`。いずれも CSV と併用できます(「PDFの自動推定と併用する」を参照)。
- 上流が GHCR(`ghcr.io/krrome/`)で公開しているイメージには、**このフォークのCSV機能は含まれません**。

## 謝辞 (Acknowledgments)

このリポジトリは、次の方々・プロジェクトの成果の上に成り立っています。心より感謝いたします。

| 対象 | 内容 |
|------|------|
| **Roman Kayan 氏 ([@krrome](https://github.com/krrome))** | 元になった [krrome/docling-serve-hierarchical](https://github.com/krrome/docling-serve-hierarchical) の作者。docling-serve の変換結果に後処理を差し込む仕組み(`patch_manager.py` による `manager.py` の自動パッチ、`Containerfile`、GitHub Actions によるイメージのビルド・公開)を作成し、MIT ライセンスで公開してくださいました。本フォークの CSV による見出しの付け直し機能は、この仕組みを土台にしています |
| **Roman Kayan 氏 ([krrome/docling-hierarchical-pdf](https://github.com/krrome/docling-hierarchical-pdf))** | PDF の目次・フォント情報から見出し階層を推定するライブラリ。`HIERARCHY_MODE=auto`(自動推定)で利用しています |
| **[docling-project](https://github.com/docling-project)** | 文書変換ライブラリ [docling](https://github.com/docling-project/docling) と、API サーバー [docling-serve](https://github.com/docling-project/docling-serve) の開発チーム |

- 元のリポジトリの著作権表示(`LICENSE`: Copyright (c) 2025 Roman Kayan、MIT License)は、そのまま保持しています。
- 本フォークでの追加・変更(CSV ルール表による見出しの付け直し、本文・自動番号の見出し昇格、PDF 自動推定との併用、日本語ドキュメント)は [lockdlock](https://github.com/lockdlock) によるものです。

## Licenses

The software in these images is MIT-licensed ([docling-serve](https://github.com/docling-project/docling-serve/blob/main/LICENSE), [docling](https://github.com/docling-project/docling/blob/main/LICENSE)) and Apache 2.0-licensed (EasyOCR). The base layer is Red Hat UBI, which [explicitly permits redistribution](https://www.redhat.com/licenses/EULA_Red_Hat_Universal_Base_Image_English_20190422.pdf).

The `-models` images additionally contain model weights from [`ds4sd/docling-models`](https://huggingface.co/ds4sd/docling-models) (layout analysis and TableFormer), which are released under [CDLA Permissive 2.0](https://cdla.dev/permissive-2-0/) and [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0). Both licenses permit redistribution; if you redistribute a `-models` image you must pass these license terms on to your recipients.

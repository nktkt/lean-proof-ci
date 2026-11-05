# Lean Proof CI — 日本語クイックスタート

Lean の宣言を読み取り、未完成の証明・許可していない公理・定理や定義の変更を検出する CLI です。
Python 3.11 以上と [elan](https://github.com/leanprover/elan#installation) が必要です。
サンプルは Lean 4.29.1 を固定しており、Mathlib や外部 AI サービスは不要です。

リポジトリのルートで実行してください。

```sh
python3 -m lean_proof_ci check examples/basic \
  --baseline examples/basic/lean-proof-ci.baseline.json
```

正常なら `PASS: 4 declarations, 0 policy violations, 0 baseline changes` と表示されます。
結果は `examples/basic/.proof-ci/report.json` と `report.md` に保存されます。

失敗を確認するサンプルもあります。

```sh
python3 -m lean_proof_ci check examples/broken
```

こちらは `sorry` と独自公理に依存しているため、終了コード **1** が正常な期待結果です。
ビルドや検査自体のエラーは **2**、検査合格は **0** です。

## 自分のプロジェクトで使う

仮想環境内で `python3 -m pip install .` を実行すると、`lean-proof-ci` コマンドを利用できます。
検査対象の Lake プロジェクトに `lean-proof-ci.toml` を置きます。

```toml
modules = ["MyLibrary"]
allowed_axioms = ["propext", "Classical.choice", "Quot.sound"]
timeout_seconds = 180
```

`modules` は名前空間ではなくインポートするモジュール名です。
`MyLibrary` と、それが読み込む `MyLibrary.*` の宣言を検査します。
別のルートも検査したい場合は配列に追加してください。
依存する証明の公理は、検査対象外のモジュールまでたどります。

最初に内容をレビューした基準ファイルを保存し、以後の検査で比較します。

```sh
lean-proof-ci snapshot /path/to/project
lean-proof-ci check /path/to/project \
  --baseline /path/to/project/lean-proof-ci.baseline.json
```

基準ファイルを Git に含め、定理の前提・結論・定義の変更を受け入れるときだけ更新してください。
CI で毎回基準ファイルを作り直すと変更検出ができません。

`sorryAx` は許可リストに追加できません。
通常の証明手順の変更や束縛変数名の変更は、命題と公理依存が同じなら合格します。
論理的に同値な別表現でも、内部構造が変わればレビューを要求する場合があります。

このツールは「自然言語の仕様どおりの定理か」を自動判定しません。
読み込まれないファイルや保存されない匿名の `example` は検査対象外です。
詳しい検査範囲と制約は [README](README.md#scope-and-limitations) を参照してください。

## テスト

```sh
python3 -m unittest discover -v
```

実際の Lean を使って、直接・間接の `sorry`、独自公理、定理の弱体化、定義の変更、
ビルド失敗などを検証します。GitHub Actions のワークフローも同梱しています。

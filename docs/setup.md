# 実行環境の準備

記事生成・テスト・ビルドに使う共通の実行環境を準備する。関心とモデルの設定は [共通設定](configuration.md)、記事生成は [arXivの記事生成](pipeline-arxiv.md)、ビルドと公開は [公開手順](publishing.md) を参照する。

## 前提

- Node.js 22.13以上とnpm
- Bashとuv
- CairoSVGが使うOS側のCairo共有ライブラリ

Cairoは [CairoSVG公式のインストール手順](https://cairosvg.org/documentation/#installation) に従ってOSのパッケージ管理で準備する。環境によってはlibffi・コンパイラー・開発用ヘッダーも必要になる。uvとbootstrapはOSのライブラリをインストールしない。Cairoを読み込めない場合はpreflightのimport確認が失敗するため、依存を整えてから再実行する。

## 依存関係を準備する

Node依存は `package.json` と `package-lock.json`、Python依存と固定バージョンは `pipeline/pyproject.toml`、`pipeline/uv.lock`、`pipeline/.python-version` に定める。Pythonは情報源を問わず共通の `pipeline/.venv/` を使う。

リポジトリのルートで次を実行する。初回・通常更新・再開のいずれも `bootstrap.sh` を実行する。`npm test` のコレクションロック・DBスキーマのテストもPythonを使うため、記事生成を行わない更新でもPython環境が必要になる。

```sh
cd /path/to/ai-platform-feedreader
export PROJECT_ROOT="$PWD"
npm ci
bash pipeline/bootstrap.sh --report "$PROJECT_ROOT/.cache/pipeline/preflight.json"
```

bootstrapは不足するPython・パッケージを準備し、正常な環境を再利用する。キャッシュは既定で `.cache/pipeline/uv`、Pythonのインストール先は `.cache/pipeline/python` を使い、`UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR` で変更できる。`preflight.py` が固定バージョンと必要なライブラリのimportを確認する。

結果は `--report` の指定先（省略時は `.cache/pipeline/preflight.json`）へ保存する。記事生成では今回の結果を各runの `preflight.json` に保存する。準備に失敗したら記録されたエラーを解消して再実行する。

画像保存・配信の設定は [公開先の設定と保護](publishing.md#公開先の設定と保護)、依存ソフトウェアのライセンスは [第三者ライセンス](../THIRD_PARTY_NOTICES.md) を参照する。

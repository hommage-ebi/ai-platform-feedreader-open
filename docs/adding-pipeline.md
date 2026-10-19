# 新しいpipelineを追加する

情報源ごとの処理を `pipeline/<source>/` に追加し、完成記事を [共通記事契約](data-contracts.md) に合わせる。実装は [pipeline/arxiv/](../pipeline/arxiv/) を参照する。

## 1. ファイルを追加する

収集経路・利用条件、安定した記事ID、版・更新日、原文・図の取得方法を情報源ごとに決める。小さなpipelineでは関連工程を一つのモジュールにまとめてもよい。

```text
pipeline/<source>/
├── __init__.py
├── workflow.py          # CLI、工程の実行・再開・状態確認
├── collect.py           # 候補の取得・検証・重複排除
├── prepare.py           # 同じ記事・版の原資料を準備
├── make_tasks.py        # 分類・解説タスクを作成
├── assemble.py          # 出力・図を検証し、記事を組立て
├── reader_adapter.py    # 共通記事形式へ変換・マージ
├── config/sources.json  # 情報源固有の収集設定
├── prompts/             # 分類・解説の指示
└── tests/               # __init__.pyとtest_pipeline.py
```

依存は共通の `pipeline/pyproject.toml` と `pipeline/uv.lock` に追加し、[共通Python環境](setup.md) を使う。CLIはルートから `uv run --project pipeline --locked --no-sync python -m pipeline.<source>.workflow` で起動する。

## 2. 共通処理を使う

- `pipeline.shared.interests`：`load_interests(root)` で関心設定を読み、`render_relevance(...)` で分類指示へ展開
- `pipeline.shared.agents`：`load_agents(root)` でモデル・推論設定・並列上限を読む。設定形式は [共通設定](configuration.md) を参照
- `pipeline.shared.runtime`：`read` / `write`（JSON）、`digest`（ハッシュ）、`lock`（排他）、`record`（工程記録）、`project_root`（ルート解決）
- `pipeline.shared.collection`：`collection_lock(dest, run)` 内で `recover_collection(dest)` により未完了更新を復旧し、`commit_collection(dest, files)` で記事コレクションを更新
- `pipeline.shared.image_store`：`store_figure(...)` で図をWebP化・保存・読戻し照合。戻り値の記録を記事の `stored_images` とmanifestに保存し、HTMLには `serving_path` を使う。[画像契約](data-contracts.md#画像のwebpr2保存契約必須)・[接続設定](publishing.md#pythonからsites管理r2へ保存する) を参照

収集・原資料の検証・タスク作成は情報源側で実装する。arXiv固有の [実行予約・run記録](pipeline-arxiv.md) は別情報源へ自動適用されないため、並行実行・再開・公開集計に参加する場合は対応を追加する。

## 3. Readerへ接続する

アダプターは完成記事を `data/articles/` へマージする。既存の記事・版・ユーザー状態を保持し、[コレクションのロックと復旧](data-contracts.md#生成runの所有権とコレクション復旧) に従う。

記事には情報源を含む安定した `id`、`source`、原文の `source_url` を持たせる。本文はHTML内の一つの `.narrative` に置き、版・画像の参照を [記事インターフェース](data-contracts.md#共通の記事インターフェース) に合わせる。

工程の組立ては [workflow.py](../pipeline/arxiv/workflow.py)、記事生成は [assemble.py](../pipeline/arxiv/assemble.py)、共通記事形式への変換は [reader_adapter.py](../pipeline/arxiv/reader_adapter.py) を参照する。

## 4. テストする

合成入力で正常系・空結果・取得失敗・重複・更新版・途中終了と再開・設定変更・壊れた出力・画像不一致・既存記事の保持を確認する。実サイトへの接続確認は分ける。

```sh
npm run test:python
npm run typecheck
npm test
npm run build
npm run test:built
npm run test:runtime
npm run verify
```

Pythonテストの各パッケージには `__init__.py` を置く。保存対象をstageしたら `npm run check:retention` を実行し、公開は [共通手順](publishing.md) に従う。

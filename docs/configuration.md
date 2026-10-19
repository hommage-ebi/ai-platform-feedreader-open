# 共通設定

関心分野と分類・解説のモデル、同時起動上限は `pipeline/config/` で設定する。収集先やカテゴリは情報源ごとの設定とし、arXivは [収集するカテゴリの設定](pipeline-arxiv.md#収集するカテゴリを設定する) を参照する。

## 関心分野

[`pipeline/config/interests.json`](../pipeline/config/interests.json) を編集する。同梱の材料研究の設定は編集可能な例であり、用途に合わせて項目の追加・削除・変更ができる。分類プロンプト自体を書き換える必要はない。

```json
{
  "schema_version": 1,
  "interests": [
    {
      "id": "battery-models",
      "label": "電池材料の計算モデル",
      "criteria": "電池材料の構造・反応・輸送特性を説明または予測する計算研究を採用する。"
    },
    {
      "id": "research-tools",
      "label": "研究支援ツール",
      "criteria": "論文の検索・検証・再現を支援する手法を採用する。"
    }
  ],
  "application_guidance": "用語の一致だけでなく研究対象・方法・目的との具体的な接点を判定する。"
}
```

- `schema_version` は整数の `1` とする
- `interests` は1項目以上の配列で、各項目は `id`、`label`、`criteria` を持つ
- `id` は重複しない安定した識別子。英数字で始め、英数字・ピリオド・アンダースコア・ハイフンからなる64文字以内の文字列にする。分類結果の `topics` にこのIDを使う
- `label` は表示名、`criteria` はその関心に含める条件・除外条件、`application_guidance` は全項目に共通の適用指針。いずれも空ではない文字列にする
- 上記以外のキー、重複キー、欠損、不正な値はエラーになる。設定ファイルがない場合も分類を止め、既定値や推測で代用しない

分類タスクは、設定した全項目と適用指針を入力へ含める。arXivでは `pipeline/arxiv/prompts/relevance.txt` へ展開し、分類結果の `topics` は設定されたIDに照合する。

## モデルと並列数

[`pipeline/config/agents.json`](../pipeline/config/agents.json) で分類と解説のモデル・推論設定、および全run共通の同時起動上限を指定する。同梱の値は次のとおり。

```json
{
  "schema_version": 1,
  "classify": {"model": "gpt-6.1-sol", "reasoning_effort": "high"},
  "summary": {"model": "gpt-6.1-sol", "reasoning_effort": "high"},
  "max_parallel": 6
}
```

`schema_version` は整数の `1`、`max_parallel` は1以上の整数とする。`classify` は分類、`summary` は論文ごとの解説に使う。`model` は英数字で始まり、英数字・ピリオド・アンダースコア・コロン・スラッシュ・ハイフンで構成される1〜128文字の識別子、`reasoning_effort` は `low`、`medium`、`high`、`xhigh`、`max`、`ultra` のいずれかを指定する。設定の欠損・重複キー・未知のキー・形式不正はエラーになる。モデル・推論設定には実行環境が対応する値を指定する。設定ファイルに書くだけでモデルの利用権限や同時実行枠が増えるわけではない。起動担当はタスクJSONの指定値と実際の利用可否を確認する。

`max_parallel` は新しいモデル実行予約を受け付けるときの共通上限として使う。現在のarXiv処理では全runの分類と解説を合わせて数え、新しい `claim` のたびに現在の値を確認する。引き下げても実行中の処理は中断せず、予約数が上限未満になるまで新しい予約を受け付けない。実行環境の制限がより小さい場合は、その制限も守る。

モデル・推論設定と関心の変更は既存処理へ遡及させない。arXivの初回設定の固定、再開時の照合、分類のグループ分割は [設定変更と再開](pipeline-arxiv.md#設定変更と再開) に従う。

## 設定の検証

共通設定は `pipeline/shared/interests.py` と `pipeline/shared/agents.py` が検証する。[実行環境の準備](setup.md) の後、リポジトリのルートで編集内容を確認できる。

```sh
uv run --project pipeline --locked --no-sync python -c 'from pipeline.shared.interests import load_interests; from pipeline.shared.agents import load_agents; load_interests("."); load_agents("."); print("Configuration valid")'
```

これは形式の検証であり、モデルの利用可否や分類結果の妥当性を確認するものではない。通常の記事更新では設定・プロンプトを変更せず、変更が必要な場合は編集した内容を確認してから新しい処理へ適用する。

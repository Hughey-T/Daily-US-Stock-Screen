# ChatGPTプロジェクト用の追記指示

以下のURLは自分のリポジトリ名に置き換える。

- 管理JSON: `https://raw.githubusercontent.com/hughey-t/Daily-US-Stock-Screen/main/docs/latest.json`
- 候補CSV: `https://raw.githubusercontent.com/hughey-t/Daily-US-Stock-Screen/main/docs/latest.csv`

スクリーニング開始時は、最初に管理JSONをWebから取得する。

以下を確認する。

- status
- generated_at
- market_data_date
- row_count
- universe_count
- data_coverage
- failed_ticker_count
- schema_version
- config_version
- config_hash
- universe_distribution
- sector_distribution

`status` が `success` でない場合は、CSVの分析を開始せず、失敗理由と最後に成功した市場日を報告する。`schema_version` は `1.2` であることを確認する。

正常なら候補CSVを取得し、価格・出来高・相対リターンに関する定量候補母集団として使用する。CSVの数値を、期間や定義が異なるWebサイトの数値で置き換えない。

Web検索は、値動きの原因、一次資料、決算、ガイダンス、契約、規制、資本政策、市場期待との不整合を確認するために使用する。

CSV外の銘柄を追加する場合は、例外採用であること、追加理由、CSVに入らなかった理由、同一基準の定量値を確認できたかを明記する。

分析開始時に、基準日、候補数、母集団数、データ取得率、取得失敗数を表示する。

Schema 1.2では、従来列に加えて次の列を分析する。

- 長期リターン: `return_63d`, `return_126d`
- SPY比較: `spy_return_63d`, `spy_return_126d`, `spy_relative_63d`, `spy_relative_126d`
- セクター比較: `sector_etf_return_63d`, `sector_etf_return_126d`, `sector_relative_63d`, `sector_relative_126d`
- ショック情報: `max_daily_move_date_21d`, `max_daily_move_signed_21d`, `max_1d_share_of_abs_move_21d`, `directional_efficiency_21d`
- ショック後リターン: `post_max_move_return_5d`, `post_max_move_return_10d`

`directional_efficiency_21d` は、21日間の対数リターン合計の絶対値を絶対対数リターン合計で割った値である。通常は0以上1以下で、1に近いほど一方向へ効率的に動き、0に近いほど上下動が相殺されている。分母が0の場合は `NaN` となる。

`return_126d` とその相対列は履歴不足で `NaN` になり得る。最大変動日からの後続データ不足時は5日後・10日後リターンも `NaN` になり得るため、欠損をゼロと解釈しない。個別候補だけでなく、`universe_distribution` と `sector_distribution` を使って母集団・同セクター内での位置を評価し、`config_version` と `config_hash` を分析結果へ記録する。

# ChatGPTプロジェクト用の追記指示

以下のURLは自分のリポジトリ名に置き換える。

- 管理JSON: `https://raw.githubusercontent.com/<USER>/<REPO>/main/docs/latest.json`
- 候補CSV: `https://raw.githubusercontent.com/<USER>/<REPO>/main/docs/latest.csv`

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

`status` が `success` でない場合は、CSVの分析を開始せず、失敗理由と最後に成功した市場日を報告する。

正常なら候補CSVを取得し、価格・出来高・相対リターンに関する定量候補母集団として使用する。CSVの数値を、期間や定義が異なるWebサイトの数値で置き換えない。

Web検索は、値動きの原因、一次資料、決算、ガイダンス、契約、規制、資本政策、市場期待との不整合を確認するために使用する。

CSV外の銘柄を追加する場合は、例外採用であること、追加理由、CSVに入らなかった理由、同一基準の定量値を確認できたかを明記する。

分析開始時に、基準日、候補数、母集団数、データ取得率、取得失敗数を表示する。

# Daily US Stock Screen

GitHub Actions が米国市場の各営業日終了後に yfinance を実行し、米国上場株の異常値動きを計算して GitHub Pages に公開するテンプレートです。

## 出力

- `docs/latest.csv`: ChatGPT に読ませる最新候補
- `docs/latest.json`: 更新日・取得品質・失敗状態
- `docs/index.html`: スマートフォン確認用ページ
- `docs/archive/`: 日次候補CSV
- `data/signal_history.csv`: シグナル継続性の計算用
- `data/universe_cache.csv`: Yahooスクリーナー障害時の短期キャッシュ

出力スキーマは version `1.2` です。`latest.json` の `config_version` は設定の識別子、`config_hash` は正規化した `config.yml` の SHA-256 であり、分析に使った設定を再現するために利用できます。

## 計算内容

- 21取引区間の価格リターン
- SPYに対する相対リターン
- セクターETFに対する相対リターン
- 最新5日平均出来高 ÷ 直前20日平均出来高
- 20日ボラティリティ ÷ 直前120日ボラティリティ
- 21日内の最大1日変動と最大ギャップ
- 52週高値・安値からの距離
- シグナル初出日、直近5・10回の出現回数、連続出現日数、前回順位との差

価格は株式分割だけを調整し、配当は含めません。

### Schema 1.2 の追加列

- `return_63d` / `return_126d`: 最新終値を63 / 126取引区間前の終値で割った価格リターン
- `spy_return_63d` / `spy_return_126d`: 同じ方式で計算したSPYの価格リターン
- `spy_relative_63d` / `spy_relative_126d`: 銘柄リターンから同期間のSPYリターンを引いた値
- `sector_etf_return_63d` / `sector_etf_return_126d`: 対応セクターETFの価格リターン
- `sector_relative_63d` / `sector_relative_126d`: 銘柄リターンから同期間のセクターETFリターンを引いた値
- `max_daily_move_date_21d`: 直近21営業日で絶対日次リターンが最大だった日
- `max_daily_move_signed_21d`: その日の符号付き日次リターン
- `max_1d_share_of_abs_move_21d`: 最大絶対日次リターンが21日間の絶対日次リターン合計に占める比率
- `directional_efficiency_21d`: 21日間の対数リターン合計の絶対値を、同期間の絶対対数リターン合計で割った値。通常は0以上1以下で、全日同方向なら1に近く、上下が相殺するほど0に近づく。分母が0の場合は `NaN`
- `post_max_move_return_5d` / `post_max_move_return_10d`: 最大変動日の終値から5 / 10営業日後までのリターン

126取引区間分の履歴がない場合、`return_126d` と対応するSPY・セクター相対列は `NaN` になります。最大変動日の後に必要な営業日数がない場合、`post_max_move_return_5d` または `post_max_move_return_10d` は `NaN` になります。列自体は常にCSVへ出力されます。

### 母集団分布

`latest.json` の `universe_distribution` は、価格・流動性条件を通過し、候補選定を行う直前の母集団について、21日リターン、SPY・セクター相対21日リターン、63日リターンの分位点と所定の閾値超過銘柄数を記録します。`sector_distribution` は各セクターの銘柄数、セクター相対21日リターンの分位点、63日リターン中央値を記録します。非有限値はJSONへ `NaN` や `Infinity` として書かず、`null` に変換します。

## 初期設定

1. GitHubで公開リポジトリを作成します。例: `daily-us-stock-screen`
2. このテンプレートの全ファイルをリポジトリの `main` ブランチへアップロードします。
3. リポジトリの `Settings` → `Actions` → `General` を開きます。
4. `Workflow permissions` を `Read and write permissions` にして保存します。
5. `Settings` → `Pages` を開きます。
6. `Build and deployment` の `Source` を `GitHub Actions` にします。
7. `Actions` タブ → `Daily US stock screen` → `Run workflow` を押します。
8. 初回実行が完了すると、`Settings` → `Pages` に公開URLが表示されます。

## 固定URL

リポジトリ名が `daily-us-stock-screen` の場合:

- ページ: `https://<GitHubユーザー名>.github.io/daily-us-stock-screen/`
- CSV: `https://raw.githubusercontent.com/<GitHubユーザー名>/daily-us-stock-screen/main/docs/latest.csv`
- JSON: `https://raw.githubusercontent.com/<GitHubユーザー名>/daily-us-stock-screen/main/docs/latest.json`

ChatGPTには、まずJSONを確認させ、`status=success` の場合だけCSVを分析させてください。

## 自動実行時刻

`.github/workflows/daily-screen.yml` は日本時間の火曜日～土曜日 07:45 に実行します。米国の月曜日～金曜日の取引終了後に対応します。新しい市場日がない場合はCSVを更新しません。

## 設定変更

`config.yml` で閾値を変更できます。主要な初期値:

- 時価総額: 10億ドル以上
- 株価: 1ドル以上
- 20日平均売買代金: 2,000万ドル以上
- SPYまたはセクター比: ±8ポイント
- 出来高倍率: 1.8倍
- ボラティリティ倍率: 1.5倍
- 1日変動・ギャップ: 7%以上

## 失敗時の動作

取得品質が基準未満の場合、既存の `latest.csv` は上書きしません。`latest.json` だけを `status=failed` に更新し、GitHub Actions も失敗として表示します。

## 注意

- yfinance は Yahoo Finance と非提携のオープンソースツールです。
- Yahoo側の仕様変更やアクセス制限で取得に失敗する場合があります。
- このCSVは候補抽出用です。投資判断には一次資料を使った個別調査が必要です。

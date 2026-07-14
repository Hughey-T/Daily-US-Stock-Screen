# Daily US Stock Screen

GitHub Actions が米国市場の各営業日終了後に yfinance を実行し、米国上場株の異常値動きを計算して GitHub Pages に公開するテンプレートです。

## 出力

- `docs/latest.csv`: イベント・異常値候補
- `docs/quiet_drift.csv`: 静かな長期ドリフト候補
- `docs/latest.json`: 更新日・取得品質・失敗状態
- `docs/index.html`: スマートフォン確認用ページ
- `docs/archive/`: 日次候補CSV
- `data/signal_history.csv`: シグナル継続性の計算用
- `data/universe_cache.csv`: Yahooスクリーナー障害時の短期キャッシュ

出力スキーマは version `1.3` です。`latest.json` の `config_version` は設定の識別子、`config_hash` は正規化した `config.yml` の SHA-256 であり、分析に使った設定を再現するために利用できます。

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

## Quiet drift経路（Schema 1.3）

`latest.csv` は従来どおり、価格変動、出来高、ボラティリティ、ギャップ等を起点にしたevent/anomaly経路です。`quiet_drift.csv` は同じ時価総額・株価・流動性通過母集団から独立して、63日または126日のセクター相対価格が静かに一方向へ積み上がった銘柄を抽出します。両経路の候補や順位は混合しません。

Quiet driftでは、株価単体について63 / 126取引区間の最大絶対日次リターン、最大絶対ギャップ、最大1日変動の絶対変動合計に対する比率、対数リターンによる方向効率、上昇日数、下落日数、上昇日比率を計算します。`max_daily_move_126d`、`max_gap_126d`、`max_1d_share_of_abs_move_126d`、`directional_efficiency_126d`、`positive_days_126d`、`negative_days_126d`、`positive_day_ratio_126d` はその126日版です。株価単体指標は、単発イベントや荒い値動きの除外に使います。方向効率は対数リターン合計の絶対値を絶対対数リターン合計で割った値で、通常0～1、分母が0なら `NaN` です。

さらに銘柄と対応セクターETFの日付を揃え、`log(銘柄終値_t / 銘柄終値_t-1) - log(ETF終値_t / ETF終値_t-1)` を日次相対対数リターンとして計算します。63 / 126日それぞれについて、最大絶対値 `relative_max_daily_move_*`、最大1日の絶対変動合計に対する比率 `relative_max_1d_share_of_abs_move_*`、相対対数リターン合計の絶対値を絶対値合計で割る `relative_directional_efficiency_*` を出力します。quiet driftの一方向性はanchor期間に対応する相対方向効率で判定します。分母0、履歴不足、銘柄とETFの日付不一致は `NaN` とし、代替値を使いません。

流動性通過母集団全体の `sector_relative_63d` / `sector_relative_126d` 上下5%とpercentile rankを基準にglobal tailを選び、quietness通過銘柄から各セクター上下1件も追加します。セクター枠は上位を先に選び、その銘柄を除いたプールから下位を選ぶため、同一銘柄にtop / bottomを重複付与しません。通過銘柄が1件だけのセクターはanchor相対リターンが正ならtop、負ならbottom、0または不明なら採用しません。不足枠の補充はしません。global tail外の `sector_coverage` は `tail_distance >= 0.50` の構造的な最低強度を必要とし、global tailにはこの追加条件を適用しません。

`selection_bucket` は `global_tail` またはglobal tail外の `sector_coverage`、`selection_reason` は該当理由をセミコロン区切りで保持します。`anchor_horizon` はpercentile中央値から遠い方の `63d` / `126d` です。`trend_consistency` は63日と126日のセクター相対リターンが非ゼロ同方向なら `same_direction`、不一致またはゼロなら `recent_regime_change`、126日不足なら `insufficient_history` です。126日anchorは126日株価quietness、126日相対quietness、`same_direction` をすべて必須とします。63日anchorは最近の転換を観測できるよう方向一致を必須にしません。順位はtail distance、anchor期間の相対方向効率、anchor相対リターン絶対値、tickerの順で決定します。

初期quietness閾値は、株価単体の最大日次変動7%、最大ギャップ7%、出来高倍率1.80未満、ボラティリティ倍率1.50未満、最大1日寄与20%以下、方向効率25%以上です。既存の `quiet_drift_min_directional_efficiency_63d` は後方互換のため株価単体の意味で維持し、相対系列には明示的に `quiet_drift_min_relative_directional_efficiency_63d/126d` を使います。126日anchorには同じ126日株価閾値を適用します。相対系列はanchor期間の最大1日寄与20%以下、相対方向効率25%以上を必要とします。最大100件、セクター上下各1件、coverage-onlyの最低tail distanceは0.50です。これらは事後検証前の仮設定であり、今回の結果へ合わせて調整していません。全設定は `latest.json` の `quiet_drift_thresholds` に記録されます。

候補が0件でも `quiet_drift.csv` と日次アーカイブは正しいヘッダー付き0行CSVとして生成され、全体は成功します。必要指標の欠損は0とみなさず候補から除外します。

Quiet drift CSVの公開URLは `https://raw.githubusercontent.com/hughey-t/Daily-US-Stock-Screen/main/docs/quiet_drift.csv` です。

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
- Quiet drift CSV: `https://raw.githubusercontent.com/<GitHubユーザー名>/daily-us-stock-screen/main/docs/quiet_drift.csv`
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

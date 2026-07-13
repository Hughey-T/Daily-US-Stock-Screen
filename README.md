# Daily US Stock Screen

GitHub Actions が米国市場の各営業日終了後に yfinance を実行し、米国上場株の異常値動きを計算して GitHub Pages に公開するテンプレートです。

## 出力

- `docs/latest.csv`: ChatGPT に読ませる最新候補
- `docs/latest.json`: 更新日・取得品質・失敗状態
- `docs/index.html`: スマートフォン確認用ページ
- `docs/archive/`: 日次候補CSV
- `data/signal_history.csv`: シグナル継続性の計算用
- `data/universe_cache.csv`: Yahooスクリーナー障害時の短期キャッシュ

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

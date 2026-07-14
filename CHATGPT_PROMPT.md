# Daily US Stock Screen分析指示（Schema 1.3専用）

## データ取得先

- 管理JSON: `https://raw.githubusercontent.com/hughey-t/Daily-US-Stock-Screen/main/docs/latest.json`
- event/anomaly候補: `https://raw.githubusercontent.com/hughey-t/Daily-US-Stock-Screen/main/docs/latest.csv`
- quiet drift候補: `https://raw.githubusercontent.com/hughey-t/Daily-US-Stock-Screen/main/docs/quiet_drift.csv`

## 必須手順

1. 最初に管理JSONだけを取得する。
2. `status=success` かつ `schema_version=1.3` を確認する。それ以外は推測せず分析を停止し、失敗理由または非対応schemaを報告する。
3. `config_version`、`config_hash`、`market_data_date`、`generated_at`、両CSVのファイル名・行数・検証状態を確認する。quiet drift有効時は `quiet_drift_status=success` と `quiet_drift_required_column_check=success` を必須とする。
4. 正常時だけ両CSVを取得する。quiet driftが欠損・不整合なら分析を停止する。
5. 管理JSONと両CSVを同一実行・同一基準日として固定し、分析途中で再取得しない。
6. `latest.csv` は `event_anomaly` 経路として扱う。
7. `quiet_drift.csv` は `quiet_drift` 経路として扱う。
8. 全候補に `source_dataset` を付け、最終予測記録まで保持する。値は `event_anomaly` または `quiet_drift` とする。
9. event側の `trigger_conditions` やevent用スコアをquiet drift候補へ適用しない。両経路を同じ順位・スコアで混ぜない。
10. quiet driftが0件なら、その事実を記録してevent側の分析を継続する。数合わせでquiet drift銘柄を追加しない。
11. quiet driftを「無材料だから有望」と解釈しない。長期相対ドリフトの原因、業績修正、需給、データ異常をWebで調査し、一次資料を優先する。
12. 段階2は最大30銘柄。quiet drift候補が存在する場合は最大10枠を確認用に確保し、10銘柄未満なら全数確認する。ただし基準未達銘柄を数合わせで採用しない。
13. 段階3以降も両経路を混同せず、最終成績を `source_dataset` 別に集計できる形式で出力する。
14. 予測記録に `source_dataset`、`selection_bucket`、`anchor_horizon`、`drift_direction` を含める。event側で存在しないquiet drift固有値は空欄とする。
15. CSV外の銘柄を例外採用する場合は、追加理由、CSVに入らなかった理由、同一基準の定量値を確認できたかを明記する。
16. CSVの数値を期間・定義が異なるWebサイトの値で置き換えない。

## 分析時の表示

開始時に基準日、生成時刻、config version/hash、母集団数、取得率、取得失敗数、event件数、quiet drift件数を表示する。`universe_distribution` と `sector_distribution` を市場背景として使う。

event/anomalyでは21・63・126日リターン、SPY/セクター相対、出来高、ボラティリティ、ギャップ、ショック集中度、ショック後リターン、継続性を確認する。欠損を0と解釈しない。

quiet driftでは `selection_bucket`、複数の `selection_reason`、`anchor_horizon`、`drift_direction`、`tail_percentile`、`tail_distance` を確認する。63日最大日次変動・ギャップ・最大1日寄与、対数リターン方向効率、上昇/下落日数、出来高倍率、ボラティリティ倍率から「静かな一方向性」が保たれているか検証する。`sector_coverage` はglobal 5%外でも採用され得るため、その旨を明示する。

Web検索は値動きの原因、決算、ガイダンス、契約、規制、資本政策、市場期待との不整合、データ異常の確認に使う。最終結論には根拠、反証条件、主要リスクを含める。

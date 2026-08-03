import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "CHATGPT_PROMPT.md"
FIXTURES = ROOT / "tests" / "fixtures" / "user-output"
INITIAL_FINALS = ("final_with_selection.md", "final_no_selection.md", "final_all_insufficient.md")
FORBIDDEN = (
    "generation_id",
    "candidate_set_id",
    "snapshot_id",
    "request_id",
    "bundle_id",
    "nonce",
    "raw_sha256",
    "canonical_sha256",
    "verified_commit_sha",
    "artifact_path",
    "manifest_path",
    "Issue #",
    "operationId",
    "```json",
)


def section_tickers(text: str, heading: str) -> set[str]:
    match = re.search(rf"## {heading}\n\n(.*?)(?=\n## |\Z)", text, re.S)
    if not match:
        raise AssertionError(f"missing section: {heading}")
    return set(re.findall(r"\b[A-Z]{2,5}\b", match.group(1)))


class UserOutputContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = PROMPT.read_text(encoding="utf-8")
        cls.fixtures = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(FIXTURES.glob("*.md"))
        }

    def test_canonical_prompt_stays_pasteable_and_defines_phase_map(self) -> None:
        self.assertEqual(len(self.prompt), 7_000)
        self.assertIn("analysis-contract-3.0-seven-user-phase-v6", self.prompt)
        titles = (
            "本日のデータ確認と調査対象の確定",
            "候補銘柄の値動きの特徴確認",
            "値動きの原因と代替説明の検討",
            "市場が説明しきれていない部分の評価",
            "機械判定と独立分析の照合",
            "現在の候補外に補足候補がないか確認",
            "最終候補、除外結果、次工程への引き継ぎ",
        )
        for title in titles:
            self.assertIn(title, self.prompt)
        self.assertIn("内部処理は8段階のまま", self.prompt)
        self.assertIn("更新3 Phase", self.prompt)
        self.assertIn("完全確認後にだけ次の `次` でPhase 5へ進む", self.prompt)

    def test_new_conversation_requires_initial_flow_before_update_flow(self) -> None:
        boundary = self.prompt.split("## 操作と境界", 1)[1].split(
            "GitHubが`FACTS`",
            1,
        )[0]
        for rule in (
            "新しい会話で最初に完全一致する `更新` を受けた場合は、常に初回Phase 1 / 全7 Phaseを開始する",
            "更新3 Phaseを開始できるのは、同じ会話内で初回Phase 7が正常完了",
            "前回結果を参照・固定・完全性確認できない",
            "新規の初回分析としてPhase 1 / 全7 Phaseを開始する",
            "`前回結果を参照できないため比較不能`のまま更新Phaseを続行・完了してはならない",
        ):
            self.assertIn(rule, boundary)
        self.assertIn("同じ会話内の正常完了済み初回結果を固定", self.prompt)

    def test_phase_three_requires_active_public_evidence_research(self) -> None:
        phase_three = self.prompt.split(
            "Phase 3では候補内の `evidence_refs` が空でも",
            1,
        )[1].split("4. **市場が説明しきれていない部分の評価**", 1)[0]
        for rule in (
            "証拠不存在とみなさず",
            "cutoff以前の公開情報を全候補について能動調査",
            "一次資料を優先",
            "信頼できる報道を補助使用",
            "銘柄ごとに実際に確認した資料種別と公表日を簡潔に示し",
            "一次資料を未確認なら確認済みと書かない",
            "`evidence_registry` へ登録",
            "評価はそのregistry IDだけを参照",
            "実際に検索して関連資料が見つからない",
            "`evidence_refs=[]`だけを理由に検索を省略",
            "全候補を一括して判断材料不足にしてはならない",
            "検索機能が一時的に使えない場合はPhase 3を完了せず",
        ):
            self.assertIn(rule, phase_three)
        self.assertIn("公開証拠のWeb検索・閲覧は禁止しない", self.prompt)

    def test_phase_three_fixture_shows_actual_source_types_and_dates(self) -> None:
        text = self.fixtures["initial_phase_3.md"]
        for phrase in (
            "実際に確認した資料種別",
            "公表日",
            "会社IRの決算資料",
            "SEC提出書類、会社プレスリリース",
            "信頼できる報道（補助資料）",
            "一次資料では未確認",
            "一次資料を確認済みとは扱いません",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("http", text)
        self.assertNotIn("registry", text)
        self.assertNotIn("{", text)

    def test_phase_five_uses_runtime_owned_reconciliation(self) -> None:
        phase_five = self.prompt.split("Phase 5は保存後の", 1)[1].split(
            "6. **現在の候補外に補足候補がないか確認**",
            1,
        )[0]
        for rule in (
            "`reconciliation_projection`",
            "`integrated_decisions`",
            "`comparison_status`",
            "`decision`",
            "`integration_basis`",
            "変更・再計算・再分類しない",
            "一致/部分一致/不一致/判断材料不足/強制除外で比較対象外",
            "`mechanical_agreement` は照合に使わず",
            "「機械側に最終候補を支持するラベルがない」ことを格下げ理由にしない",
        ):
            self.assertIn(rule, phase_five)

    def test_phase_seven_uses_exclusive_runtime_decisions(self) -> None:
        phase_seven = self.prompt.split(
            "7. **最終候補、除外結果、次工程への引き継ぎ**",
            1,
        )[1].split("最終末尾は正確に：", 1)[0]
        for rule in (
            "保存後runtimeの`integrated_decisions[].decision`だけを正本",
            "正式対象を最終候補、追加調査、監視、判断材料不足、データ問題除外、説明済み除外の排他的な1区分へ分類",
            "主要区分合計を正式対象数と一致",
            "補足候補は別集計",
            "最終分類に`comparison_status`を使わず",
            "`RESEARCH_PRIORITY`と`MONITOR`",
            "判断材料不足へ重複掲載しない",
            "真の`decision=INSUFFICIENT_EVIDENCE`だけ",
        ):
            self.assertIn(rule, phase_seven)

    def test_all_required_fixtures_exist_and_hide_internal_details(self) -> None:
        expected = {
            "initial_phase_1.md",
            "initial_phase_3.md",
            "persistence_pending.md",
            "final_with_selection.md",
            "final_no_selection.md",
            "final_all_insufficient.md",
            "partial_data_warning.md",
            "retryable_error.md",
            "terminal_error.md",
            "update_phase_3.md",
        }
        self.assertEqual(expected, set(self.fixtures))
        for name, text in self.fixtures.items():
            with self.subTest(fixture=name):
                for token in FORBIDDEN:
                    self.assertNotIn(token, text)

    def test_initial_phase_one_counts_are_consistent(self) -> None:
        text = self.fixtures["initial_phase_1.md"]
        counts = {
            label: int(re.search(rf"\| {label} \| (\d+) \|", text).group(1))
            for label in ("急な値動き", "中長期の継続的な値動き", "合計")
        }
        self.assertEqual(
            counts["急な値動き"] + counts["中長期の継続的な値動き"],
            counts["合計"],
        )

    def test_initial_final_responses_have_exact_completion_without_next(self) -> None:
        for name in INITIAL_FINALS:
            text = self.fixtures[name]
            with self.subTest(fixture=name):
                self.assertIn("全体進捗：7 / 7 Phase 完了", text)
                self.assertIn("追加の操作は必要ありません。", text)
                self.assertIn("以上で本日のDaily US Stock Screenはすべて完了です。", text)
                self.assertNotIn("「次」と送信してください", text)

    def test_no_selection_is_normal_completion_and_has_no_handoff(self) -> None:
        text = self.fixtures["final_no_selection.md"]
        for phrase in (
            "本日の条件を満たす最終候補はありませんでした",
            "これは処理失敗ではありません",
            "市場データ確認",
            "値動きの原因調査",
            "独立評価",
            "分析結果の保存確認",
            "機械判定との照合",
            "個別株分析条件を満たす銘柄がなかった",
            "保存完了状態：分析結果の保存と完全性確認が完了",
            "## 個別株分析へ引き継ぐ対象\n\n該当なし",
        ):
            self.assertIn(phrase, text)

    def test_all_insufficient_is_not_an_error(self) -> None:
        text = self.fixtures["final_all_insufficient.md"]
        for phrase in (
            "本日の最終候補はありません",
            "スクリーニング自体は正常に完了",
            "個別株分析へ進むための証拠が不足",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("異常終了", text)

    def test_final_analysis_basis_has_four_required_items(self) -> None:
        for name in INITIAL_FINALS:
            text = self.fixtures[name]
            with self.subTest(fixture=name):
                for label in (
                    "市場データ日：",
                    "公開情報の期限：",
                    "分析基準日時：",
                    "保存完了状態：",
                ):
                    self.assertIn(label, text)

    def test_pending_stays_in_phase_four_and_only_rechecks(self) -> None:
        text = self.fixtures["persistence_pending.md"]
        self.assertIn(
            "# Phase 4 / 全7 Phase\n## 市場が説明しきれていない部分の評価",
            text,
        )
        self.assertNotIn("## 分析結果の保存確認", text)
        for phrase in (
            "分析は完了",
            "保存結果を確認中",
            "次Phaseには進めません",
            "同じ保存結果だけを確認",
            "全体進捗：4 / 7 Phaseを処理中",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("Phase 5完了", text)
        self.assertNotIn("新規保存要求", text)
        self.assertNotIn("すべての分析と保存確認が完了", text)

    def test_internal_persistence_contract_remains_complete(self) -> None:
        contract = self.prompt.split("## 内部提出・保存契約（表示しない）", 1)[1]
        artifact_fields = (
            "analysis_contract_version",
            "generation_id",
            "candidate_set_id",
            "evidence_cutoff",
            "assessments",
        )
        issue_fields = (
            "operation",
            "request_id",
            "nonce",
            "repository",
            "source_snapshot_path",
            "submitted_at",
            "artifact",
            "evidence_registry",
        )
        self.assertRegex(
            contract,
            rf"`artifact` は正確に .*{'.*'.join(artifact_fields)}.*だけ",
        )
        self.assertRegex(
            contract,
            rf"Issue本文は厳密なJSONで、正確に .*{'.*'.join(issue_fields)}",
        )
        for rule in (
            "`operation`: `persist_ai_assessment_v3`",
            "`repository`: `Hughey-T/Daily-US-Stock-Screen`",
            "`source_snapshot_path`: `docs/` + 固定manifestの`snapshot_path`",
            "canonical JSON配列 `[artifact,evidence_registry]`",
            "lowercase SHA-256に`analysis_`を付ける",
            "Issueタイトルは `[GPT-ANALYSIS-V3] ` + そのID",
            "本文はMarkdown・code fenceなしのJSONだけ",
            "`submitAnalysisWriteRequest`は1回だけ呼ぶ",
            "同じIssueを固定",
            "再分析・再提出せずPhase 5へ進まない",
            "global partと全candidate partを取得",
            "候補の不足・重複・余分を拒否",
            "完全確認後にだけPhase 4を完了",
            "機械開示前の`mechanical_agreement`は必ず`INSUFFICIENT_EVIDENCE`",
            "Phase 5では使用しない",
        ):
            self.assertIn(rule, contract)

    def test_terminal_error_cannot_be_mistaken_for_no_selection(self) -> None:
        text = self.fixtures["terminal_error.md"]
        for phrase in (
            "データの正しさを確認できない",
            "結果を利用できない",
            "これは「候補なし」ではありません",
            "今回は完了扱いにしません",
            "修正後に更新",
        ):
            self.assertIn(phrase, text)

    def test_final_classifications_are_exclusive_and_counted(self) -> None:
        text = self.fixtures["final_with_selection.md"]
        headings = (
            "最終候補",
            "追加調査を優先する候補",
            "監視候補",
            "判断材料不足",
            "除外した銘柄",
        )
        classification_sets = [section_tickers(text, heading) for heading in headings]
        for index, left in enumerate(classification_sets):
            for right in classification_sets[index + 1 :]:
                self.assertFalse(left & right)

        formal = set().union(*classification_sets)
        supplemental = section_tickers(text, "補足候補")
        handoff = section_tickers(text, "個別株分析へ引き継ぐ対象")
        declared = int(re.search(r"正式対象(\d+)銘柄", text).group(1))

        self.assertEqual(declared, len(formal))
        self.assertEqual({"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"}, formal)
        self.assertEqual({"GGG"}, supplemental)
        self.assertFalse(formal & supplemental)
        self.assertEqual(section_tickers(text, "最終候補"), handoff)

    def test_only_true_insufficient_decision_appears_in_that_section(self) -> None:
        text = self.fixtures["final_with_selection.md"]
        insufficient = section_tickers(text, "判断材料不足")
        research = section_tickers(text, "追加調査を優先する候補")
        monitor = section_tickers(text, "監視候補")
        self.assertEqual({"DDD"}, insufficient)
        self.assertEqual({"BBB"}, research)
        self.assertEqual({"CCC"}, monitor)
        self.assertFalse(insufficient & (research | monitor))


if __name__ == "__main__":
    unittest.main()

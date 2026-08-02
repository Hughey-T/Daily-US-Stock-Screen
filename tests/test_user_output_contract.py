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


class UserOutputContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = PROMPT.read_text(encoding="utf-8")
        cls.fixtures = {path.name: path.read_text(encoding="utf-8") for path in sorted(FIXTURES.glob("*.md"))}

    def test_canonical_prompt_stays_pasteable_and_defines_phase_map(self) -> None:
        self.assertLessEqual(len(self.prompt), 8_000)
        self.assertIn("analysis-contract-3.0-seven-user-phase-v1", self.prompt)
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
        self.assertIn("更新は3 Phase", self.prompt)
        self.assertIn("完全確認後にだけ次の `次` でPhase 5へ進む", self.prompt)

    def test_all_required_fixtures_exist_and_hide_internal_details(self) -> None:
        expected = {
            "initial_phase_1.md",
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

    def test_no_selection_is_successfully_persisted_and_has_no_handoff(self) -> None:
        text = self.fixtures["final_no_selection.md"]
        for phrase in (
            "本日の条件を満たす最終候補はありませんでした",
            "これは処理失敗ではありません",
            "保存状態：分析結果の保存と確認が完了",
            "## 個別株分析へ引き継ぐ対象\n\n該当なし",
        ):
            self.assertIn(phrase, text)

    def test_all_insufficient_is_not_an_error(self) -> None:
        text = self.fixtures["final_all_insufficient.md"]
        for phrase in (
            "本日の最終候補はありません",
            "スクリーニング自体は正常に完了",
            "投資判断へ進むための証拠が不足",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("異常終了", text)

    def test_pending_stays_in_phase_four_and_only_rechecks(self) -> None:
        text = self.fixtures["persistence_pending.md"]
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

    def test_final_classifications_do_not_overlap(self) -> None:
        text = self.fixtures["final_with_selection.md"]
        sections = {}
        headings = ("最終候補", "監視候補", "判断材料不足", "除外した銘柄", "補足候補", "個別株分析へ引き継ぐ対象")
        for heading in headings:
            match = re.search(rf"## {heading}\n\n(.*?)(?=\n## |\Z)", text, re.S)
            sections[heading] = set(re.findall(r"\b[A-Z]{2,5}\b", match.group(1)))
        classification_sets = [sections[heading] for heading in headings[:5]]
        for index, left in enumerate(classification_sets):
            for right in classification_sets[index + 1 :]:
                self.assertFalse(left & right)
        self.assertEqual(sections["最終候補"], sections["個別株分析へ引き継ぐ対象"])
        self.assertFalse(sections["補足候補"] & sections["個別株分析へ引き継ぐ対象"])


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1] / "CHATGPT_PROMPT.md"


class Phase4PersistenceCheckpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = PROMPT.read_text(encoding="utf-8")
        cls.phase4 = cls.prompt.split("## Phase 4保存の必須手順", 1)[1].split("## 通常回答", 1)[0]

    def test_prompt_exactly_fits_custom_gpt_limit(self) -> None:
        self.assertEqual(len(self.prompt), 7_000)
        self.assertIn("analysis-contract-3.0-seven-user-phase-v6", self.prompt)

    def test_pending_status_requires_successful_submission(self) -> None:
        for rule in (
            "ユーザーへ「保存処理を開始」「保存結果を確認中」と表示する前に",
            "`submitAnalysisWriteRequest`は1回だけ呼ぶ",
            "Actionを呼ばずに保存開始・提出済み・確認中と表示してはならない",
            "Issue番号未取得",
            "title/body未確認",
            "ここまで成功した後だけ、Phase 4保存確認中の回答を表示できる",
        ):
            self.assertIn(rule, self.phase4)

    def test_checkpoint_is_reused_without_resubmission(self) -> None:
        for rule in (
            "非表示チェックポイントとして保持",
            "同じIssue番号と`request_id`だけを使い",
            "`listAnalysisWriteRequestReceipts`",
            "再分析・再提出せずPhase 5へ進まない",
            "nonce再生成",
            "新規Issueも禁止",
        ):
            self.assertIn(rule, self.phase4)

    def test_missing_checkpoint_is_not_false_terminal_integrity_error(self) -> None:
        for rule in (
            "単にチェックポイントを思い出せないことを回復不能な整合性異常とみなしてはならない",
            "直前Action応答と固定済みrequest情報から復元を試み",
            "保存先を再確認できないため一時停止",
            "Action未実行を整合性異常として偽装しない",
        ):
            self.assertIn(rule, self.phase4)


if __name__ == "__main__":
    unittest.main()

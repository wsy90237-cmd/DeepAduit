"""底稿草稿：级次筛选、序时账事实、导出与引用清理。"""

import unittest
from decimal import Decimal

from tb_autofill import analysis, dxn, papers
from tb_autofill.models import JournalRow, LedgerRow


def ledger():
    def row(code, name, opening, ending, debit, credit, level=1, path=None):
        return LedgerRow(code=code, name=name, path=path or name, level=level, direction="借",
                         opening=Decimal(opening), ending=Decimal(ending),
                         debit=Decimal(debit), credit=Decimal(credit), subject_id="S1", year=2026, month=9)

    return [
        row("102", "银行存款", "5311172.67", "2301155.37", "2769026.51", "5779043.81"),
        row("10201", "信用社", "2416463.18", "259451.44", "1000", "3000", level=2, path="银行存款/信用社"),
        row("10206", "农业发展银行", "2894709.49", "2041703.93", "900", "2000", level=2,
            path="银行存款/农业发展银行"),
        row("10207", "空账户", "0", "0", "0", "0", level=2, path="银行存款/空账户"),
        row("6602", "管理费用", "0", "120000", "120000", "0"),
    ]


def journal():
    """借贷合计恰好等于 102 的 2,769,026.51 / 5,779,043.81，用于验证勾稽。"""
    def row(month, voucher, line, summary, code, debit="0", credit="0", contra=""):
        return JournalRow(month=month, date=f"2026-{month:02d}-28", voucher=voucher, line=line,
                          summary=summary, code=code, debit=Decimal(debit), credit=Decimal(credit),
                          contra=contra)

    return [
        row(1, "记-1", "1", "收货款", "10201", debit="2269026.51", contra="应收账款"),
        row(1, "记-2", "1", "收林地租金", "10201", debit="500000", contra="应收账款"),
        row(5, "记-20", "3", "付林地使用费", "10206", credit="5778043.81", contra="应付账款"),
        row(9, "记-31", "2", "付水电费", "10201", credit="1000", contra="管理费用"),
    ]


class LedgerRowForJournal:
    """序时账合计恰为 102 的借贷发生额，用于验证勾稽分支。"""

    @staticmethod
    def subject():
        return next(row for row in ledger() if row.code == "102")


class LevelTests(unittest.TestCase):
    def test_only_requested_level_returned(self):
        self.assertEqual([code for code, _label in papers.account_choices(ledger(), level=1)], ["102", "6602"])

    def test_level_one_comes_first(self):
        codes = [code for code, _label in papers.account_choices(ledger())]
        self.assertEqual(codes[0], "102")
        self.assertIn("10201", codes)

    def test_zero_amount_parent_is_skipped(self):
        codes = [code for code, _label in papers.account_choices(ledger())]
        self.assertIn("10207", codes)      # 叶子科目即使为零余额也保留

    def test_empty_ledger_has_no_choices(self):
        self.assertEqual(papers.account_choices([]), [])

    def test_level_options_follow_ledger(self):
        options = papers.level_options(ledger())
        self.assertEqual([level for level, _label in options], [0, 1, 2])
        self.assertEqual(dict(options)[1], "一级科目")

    def test_label_states_level(self):
        self.assertEqual(papers.level_label(1), "一级科目")
        self.assertEqual(papers.level_label(0), "全部级次")
        self.assertEqual(papers.level_label(4), "4级科目")


class JournalFactTests(unittest.TestCase):
    def test_totals_tie_to_ledger(self):
        facts = papers._journal_facts(journal(), LedgerRowForJournal.subject())
        self.assertTrue(facts["tie"]["checked"])
        self.assertTrue(facts["tie"]["debit_matches"])
        self.assertTrue(facts["tie"]["credit_matches"])

    def test_mismatch_is_reported(self):
        target = LedgerRowForJournal.subject()
        wrong = LedgerRow(code=target.code, name=target.name, level=1, direction="借",
                          opening=target.opening, ending=target.ending,
                          debit=Decimal("1"), credit=target.credit, subject_id="S1", year=2026, month=9)
        facts = papers._journal_facts(journal(), wrong)
        self.assertFalse(facts["tie"]["debit_matches"])
        self.assertTrue(facts["tie"]["credit_matches"])

    def test_incomplete_ledger_skips_tie(self):
        target = LedgerRow(code="102", name="银行存款", level=1, opening=None, ending=None,
                           debit=None, credit=None, subject_id="S1", year=2026, month=9)
        self.assertFalse(papers._journal_facts(journal(), target)["tie"]["checked"])

    def test_monthly_and_contra_grouping(self):
        facts = papers._journal_facts(journal(), None)
        self.assertEqual([item["month"] for item in facts["monthly"]], [1, 5, 9])
        self.assertEqual(facts["monthly"][0]["count"], 2)
        self.assertEqual(facts["top_contra"][0]["name"], "应付账款")
        self.assertEqual(facts["top_contra"][0]["side"], "贷方")
        self.assertEqual(facts["top_contra"][0]["amount"], "5,778,043.81")
        self.assertEqual(facts["top_contra"][1]["count"], 2)

    def test_contra_is_split_by_side(self):
        rows = [
            JournalRow(month=1, date="2026-01-31", voucher="记-1", line="1", summary="收", code="10201",
                       debit=Decimal("100"), credit=Decimal("0"), contra="应收账款"),
            JournalRow(month=2, date="2026-02-28", voucher="记-2", line="1", summary="退", code="10201",
                       debit=Decimal("0"), credit=Decimal("40"), contra="应收账款"),
        ]
        facts = papers._journal_facts(rows, None)
        sides = {(item["name"], item["side"]): item["amount"] for item in facts["top_contra"]}
        self.assertEqual(sides[("应收账款", "借方")], "100")
        self.assertEqual(sides[("应收账款", "贷方")], "40")

    def test_samples_are_bounded_and_unique(self):
        facts = papers._journal_facts(journal(), None)
        keys = [(item["date"], item["voucher"], item["line"]) for item in facts["samples"]]
        self.assertLessEqual(len(keys), papers.JOURNAL_SAMPLES)
        self.assertEqual(len(keys), len(set(keys)))

    def test_facts_include_journal_only_when_given(self):
        self.assertNotIn("journal", papers.build_facts(ledger(), "102"))
        facts = papers.build_facts(ledger(), "102", journal())
        self.assertEqual(facts["journal"]["count"], 4)
        self.assertEqual(facts["subject"]["level_label"], "一级科目")
        self.assertEqual([item["code"] for item in facts["children"]], ["10201", "10206", "10207"])

    def test_unknown_subject_still_raises(self):
        with self.assertRaises(ValueError):
            papers.build_facts(ledger(), "9999")


class StubClient:
    def __init__(self, answer="草稿正文", intent="audit"):
        self.answer = answer
        self.intent = intent
        self.contexts = []

    def converse_with_skills(self, question, context=None, **_kwargs):
        self.contexts.append(context)
        return {"intent": self.intent, "answer": self.answer, "warnings": [], "citation_warnings": []}


class DraftTests(unittest.TestCase):
    def test_journal_flows_into_context(self):
        client = StubClient()
        result = papers.draft(client, rows=ledger(), code="102", journal=journal())
        sent = client.contexts[0]
        self.assertEqual(sent["facts"]["journal"]["count"], 4)
        self.assertTrue(result["has_journal"])
        self.assertEqual(sent["constraints"]["amount_source"], "账套与序时账")

    def test_invented_citations_are_removed(self):
        client = StubClient(answer="结论见[K1]，另见[K9]。")
        result = papers.draft(client, rows=ledger(), code="102")
        self.assertNotIn("[K9]", result["body"])
        self.assertIn("[K1]", result["body"])
        self.assertTrue(any("K9" in note for note in result["warnings"]))

    def test_missing_citation_is_not_flagged_when_absent(self):
        result = papers.draft(StubClient(answer="无引用正文"), rows=ledger(), code="102")
        self.assertEqual(result["warnings"], [])

    def test_offtopic_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            papers.draft(StubClient(intent="offtopic"), rows=ledger(), code="102")


class HtmlTests(unittest.TestCase):
    def draft(self, **extra):
        facts = papers.build_facts(ledger(), "102", journal())
        value = {"kind_label": "科目审定表", "subject": facts["subject"], "body": "正文",
                 "refs": [], "journal": facts["journal"], "has_journal": True,
                 "generated_at": "2026-11-02 17:00"}
        value.update(extra)
        return value

    def test_level_and_journal_are_rendered(self):
        html = papers.to_html("科目审定表", self.draft(), subject_label="测试林场")
        self.assertIn("级次：一级科目", html)
        self.assertIn("序时账：4 笔", html)
        self.assertIn("勾稽一致", html)

    def test_without_journal_no_journal_block(self):
        value = self.draft(journal=None, has_journal=False)
        html = papers.to_html("科目审定表", value)
        self.assertNotIn("序时账：", html)

    def test_subject_label_is_escaped(self):
        html = papers.to_html("科目审定表", self.draft(), subject_label="<script>x</script>")
        self.assertNotIn("<script>x</script>", html)


class FactCrossCheckTests(unittest.TestCase):
    """序时账金额来自本机数据，模型照抄不得被当成编造金额。"""

    def facts(self):
        return papers.build_facts(ledger(), "102", journal())

    def test_journal_amounts_are_known(self):
        facts = self.facts()
        amounts = analysis.fact_amounts(facts)
        text = "本期借方 2,769,026.51 元，贷方 5,779,043.81 元，共 4 笔，最大对方科目 5,778,043.81 元。"
        self.assertEqual(analysis.suspicious_numbers(
            text, amounts, analysis.fact_codes(facts), extra=analysis.fact_tokens(facts)), [])

    def test_invented_amount_still_flagged(self):
        facts = self.facts()
        found = analysis.suspicious_numbers(
            "期末余额 9,999,999.99 元", analysis.fact_amounts(facts), analysis.fact_codes(facts),
            extra=analysis.fact_tokens(facts))
        self.assertEqual(found, ["9,999,999.99"])

    def test_child_and_sample_codes_are_known(self):
        codes = analysis.fact_codes(self.facts())
        self.assertIn("10201", codes)
        self.assertIn("10206", codes)
        self.assertIn("102", codes)


class JournalCodeTests(unittest.TestCase):
    def test_numeric_codes_pass(self):
        self.assertEqual(dxn._journal_code("102"), "102")
        self.assertEqual(dxn._journal_code(10201), "10201")

    def test_wildcards_and_injection_are_rejected(self):
        for value in ("", "102%", "102 OR 1=1", "1'; DROP TABLE c_cwpz;--", "abc"):
            with self.assertRaises(ValueError, msg=value):
                dxn._journal_code(value)


if __name__ == "__main__":
    unittest.main()

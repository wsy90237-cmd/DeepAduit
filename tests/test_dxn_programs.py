"""审计程序库：收录范围、合并去重、结论口径。"""

import unittest

from tb_autofill import dxn_programs, paper_pack


class LibraryTests(unittest.TestCase):
    def test_library_is_bundled(self):
        stats = dxn_programs.stats()
        self.assertGreaterEqual(stats["papers"], 60)
        self.assertGreaterEqual(stats["programs"], 500)

    def test_cash_has_dxn_programs(self):
        items = dxn_programs.library("1-010")
        self.assertGreaterEqual(len(items), 20)
        self.assertTrue(any("监盘库存现金" in item["text"] for item in items))

    def test_receivable_has_factoring_program(self):
        items = dxn_programs.library("1-070")
        self.assertTrue(any("函证" in item["text"] for item in items))

    def test_unknown_paper_returns_empty(self):
        self.assertEqual(dxn_programs.library("9-999"), [])

    def test_source_label_points_to_dxn_file(self):
        self.assertIn("货币资金", dxn_programs.source_label("1-010"))


class MergeTests(unittest.TestCase):
    def test_duplicates_are_removed(self):
        base = [{"text": "核对总账与明细账。", "assertions": ["完整性"]}]
        supplements = [{"text": "核对总账与明细账", "assertions": []},
                       {"text": "检查期后事项。", "assertions": ["存在"]}]
        merged = dxn_programs.merge(base, supplements)
        self.assertEqual([item["text"] for item in merged],
                         ["核对总账与明细账。", "检查期后事项。"])

    def test_limit_is_respected(self):
        base = [{"text": f"程序{i}", "assertions": []} for i in range(100)]
        self.assertEqual(len(dxn_programs.merge(base, limit=10)), 10)

    def test_blank_text_is_dropped(self):
        merged = dxn_programs.merge([{"text": "  "}, {"text": "有效程序"}])
        self.assertEqual(len(merged), 1)


class ProcedureDepthTests(unittest.TestCase):
    def test_money_fund_program_depth(self):
        items = paper_pack.procedures("1-010", "货币资金")
        self.assertGreaterEqual(len(items), 30)
        self.assertTrue(any(item.section for item in items))

    def test_revenue_program_depth(self):
        self.assertGreaterEqual(len(paper_pack.procedures("4-010", "营业收入")), 25)

    def test_unknown_category_still_has_programs(self):
        items = paper_pack.procedures("1-999", "自设科目")
        self.assertGreaterEqual(len(items), 5)
        self.assertTrue(all(item.text.strip() for item in items))


class ConclusionTests(unittest.TestCase):
    def test_balanced_conclusion_states_pending_evidence(self):
        text = paper_pack.default_conclusion(True)
        self.assertIn("勾稽一致", text)
        self.assertIn("外部证据", text)

    def test_mismatch_conclusion_requires_follow_up(self):
        self.assertIn("须追查", paper_pack.default_conclusion(False))

    def test_preview_includes_conclusion_section(self):
        text = papers_preview()
        self.assertIn("【审计结论】", text)
        self.assertIn("需进一步程序", text)


def papers_preview():
    from tb_autofill import papers
    return papers.pack_preview({"guide_notes": ["1、核对一致"],
                                "conclusion": "需进一步程序：函证待补。"})

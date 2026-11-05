"""报表项目的一致来源、独立发生额与客商维度，全部使用合成数据。"""

from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tb_autofill import dxn
from tb_autofill.models import JournalRow, JournalRows, LedgerRow, PaperData, Subject


def subject(month=2):
    return Subject("2", "合成企业", 2026, month, "synthetic", ledger_id="18")


def record(month=1, *, code="6602", name="管理费用", direction="借", amount="7", **values):
    result = {"kjmonth": month, "kmbh": code, "kmmc": name, "jdfx": direction,
              "qcs": "0", "qms": "0", "jffse": "100", "dffse": "100", "fse": amount,
              "kmjc": 1, "sjkmmc": ""}
    result.update(values)
    return result


def account(code="112201", name="应收货款", path="应收账款/应收货款", opening="100", ending="100", level=2):
    return LedgerRow(code, name, path=path, opening=Decimal(opening), ending=Decimal(ending),
                     level=level, subject_id="2", ledger_id="18", year=2026, month=2)


def config(code="112201", dimension="khhs", name="客户", selected="1"):
    return {"kmbh": code, "hsxmlxbh": dimension, "hsxmlxmc": name, "xz": selected}


def party(month=1, *, code="112201", dimension="khhs", identifier="SYN-A", name="合成客商甲", **values):
    result = {"kjmonth": month, "kmbh": code, "kmmc": "应收货款", "jdfx": "借",
              "hsxmlxbh": dimension, "hsxmlxmc": "客户", "hsxmbh": identifier, "hsxmmc": name,
              "hsxmjc": 1, "hsxmsfmx": "1", "qcs": "100", "qms": "100", "jffse": "0", "dffse": "0"}
    result.update(values)
    return result


class OccurrenceTests(unittest.TestCase):
    def test_independent_occurrence_survives_closing_and_reversals(self):
        row = dxn._aggregate([record(amount="12"), record(2, amount="-5")], subject())[0]
        self.assertEqual(row.debit, Decimal(200))
        self.assertEqual(row.credit, Decimal(200))
        self.assertEqual(row.period_amount, Decimal(7))
        self.assertEqual(row.monthly_amounts[:3], (Decimal(12), Decimal(-5), None))
        self.assertIn("c_kmyek.fse", row.period_source)

    def test_credit_income_uses_independent_source_without_direction_flip(self):
        row = dxn._aggregate([record(code="6001", name="主营业务收入", direction="贷", amount="-7")], subject(1))[0]
        self.assertEqual(row.period_amount, Decimal(-7))
        self.assertTrue(row.is_profit_loss)

    def test_missing_month_or_missing_fse_is_unknown_instead_of_gross_or_zero(self):
        for records in ([record()], [record(), record(2, amount=None)]):
            with self.subTest(records=records):
                row = dxn._aggregate(records, subject())[0]
                self.assertIsNone(row.period_amount)
                self.assertIsNone(row.monthly_amounts[1])
                self.assertEqual(row.period_source, "")

    def test_current_and_previous_monthly_occurrences_use_same_cutoff(self):
        rows = dxn._aggregate([record(amount="12"), record(2, amount="8")], subject())
        result = dxn._paper_occurrences(rows, [record(amount="4"), record(2, amount="5")], [], subject())[0]
        self.assertEqual(result.period_amount, Decimal(20))
        self.assertEqual(result.previous_period_amount, Decimal(9))
        self.assertEqual(result.previous_monthly_amounts[:3], (Decimal(4), Decimal(5), None))

    def test_annual_summary_is_not_a_midyear_same_period_comparison(self):
        rows = dxn._aggregate([record(amount=None), record(2, amount=None)], subject())
        result = dxn._paper_occurrences(rows, [], [{"kmbh": "6602", "ws_fse": "100", "sn_ws_fse": "90"}], subject())[0]
        self.assertIsNone(result.period_amount)
        self.assertIsNone(result.previous_period_amount)

    def test_annual_source_can_supply_annual_total_without_fabricating_months(self):
        rows = dxn._aggregate([record(month, amount=None) for month in range(1, 13)], subject(12))
        result = dxn._paper_occurrences(rows, [], [{"kmbh": "6602", "ws_fse": "100", "sn_ws_fse": "90"}], subject(12))[0]
        self.assertEqual((result.period_amount, result.previous_period_amount), (Decimal(100), Decimal(90)))
        self.assertEqual(result.monthly_amounts, (None,) * 12)
        self.assertEqual(result.previous_monthly_amounts, (None,) * 12)
        self.assertIn("未审", result.previous_period_source)

    def test_changed_prior_direction_and_duplicate_annual_source_are_not_assumed(self):
        rows = dxn._aggregate([record()], subject(1))
        result = dxn._paper_occurrences(rows, [record(direction="贷")], [], subject(1))[0]
        self.assertIsNone(result.previous_period_amount)
        with self.assertRaisesRegex(ValueError, "重复来源"):
            dxn._paper_occurrences(rows, [], [{"kmbh": "6602"}, {"kmbh": "6602"}], subject(1))

    def test_prior_identity_conflict_clears_amount_and_months_including_annual_fallback(self):
        for month in (1, 12):
            current = dxn._aggregate([record(name="办公费")], subject(month))
            for old in (record(name="差旅费", amount="90"),
                        record(name="办公费", kmjc=2, amount="90"),
                        record(name="办公费", direction="贷", amount="90")):
                with self.subTest(month=month, old=old):
                    result = dxn._paper_occurrences(current, [old],
                             [{"kmbh": "6602", "ws_fse": "100", "sn_ws_fse": "90"}], subject(month))[0]
                    self.assertIsNone(result.previous_period_amount)
                    self.assertEqual(result.previous_monthly_amounts, (None,) * 12)
                    self.assertIn("待确认", result.previous_period_source)

    def test_prior_parent_change_is_ambiguous_but_whitespace_is_not(self):
        current_records = [record(), record(code="660201", name="办公费", kmjc=2)]
        current = dxn._aggregate(current_records, subject(1))
        previous = [record(name="管理 费用"), record(code="660201", name="办公 费", kmjc=2)]
        result = dxn._paper_occurrences(current, previous, [], subject(1))
        self.assertEqual(result[1].previous_period_amount, Decimal(7))
        previous[0]["kmmc"] = "销售费用"
        result = dxn._paper_occurrences(current, previous, [], subject(1))
        self.assertIsNone(result[1].previous_period_amount)
        self.assertEqual(result[1].previous_monthly_amounts, (None,) * 12)


class AuxiliaryTests(unittest.TestCase):
    def test_selected_customer_dimension_does_not_sum_project_dimension(self):
        records = [party(1), party(2), party(1, dimension="xmhs"), party(2, dimension="xmhs")]
        configs = [config(), config(dimension="xmhs", name="项目")]
        rows, checks = dxn._auxiliary_balances(records, configs, [account()], subject())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].name, rows[0].type_code), ("合成客商甲", "khhs"))
        self.assertEqual(checks[0]["status"], "matched")
        self.assertEqual(rows[0].ledger_id, "18")

    def test_numeric_type_code_is_read_from_explicit_configuration(self):
        configs = [config(dimension="1", name="人员档案"), config(dimension="009", name="往来单位")]
        records = [party(1, dimension="009"), party(2, dimension="009"), party(1, dimension="1")]
        rows, _ = dxn._auxiliary_balances(records, configs, [account()], subject())
        self.assertEqual([row.type_code for row in rows], ["009"])

    def test_unselected_or_multiple_counterparty_dimensions_are_not_combined(self):
        rows, checks = dxn._auxiliary_balances([party(1), party(2)], [config(selected="0")], [account()], subject())
        self.assertFalse(rows)
        configs = [config(), config(dimension="gys", name="供应商")]
        rows, checks = dxn._auxiliary_balances([party(1), party(2)], configs, [account()], subject())
        self.assertFalse(rows)
        self.assertEqual(checks[0]["status"], "ambiguous")

    def test_customer_hierarchy_only_uses_marked_leaves(self):
        records = [party(1, identifier="ROOT", name="合成分组", hsxmsfmx="0"),
                   party(2, identifier="ROOT", name="合成分组", hsxmsfmx="0"),
                   party(1, hsxmjc=2), party(2, hsxmjc=2)]
        rows, checks = dxn._auxiliary_balances(records, [config()], [account()], subject())
        self.assertEqual([row.code for row in rows], ["SYN-A"])
        self.assertEqual(checks[0]["status"], "matched")
        records[0]["hsxmsfmx"] = ""
        rows, checks = dxn._auxiliary_balances(records, [config()], [account()], subject())
        self.assertFalse(rows)
        self.assertEqual(checks[0]["status"], "ambiguous")

    def test_parent_and_child_account_auxiliary_totals_are_not_added_twice(self):
        root = account("1122", "应收账款", "应收账款", level=1)
        records = [party(1, code="1122"), party(2, code="1122"), party(1), party(2)]
        rows, checks = dxn._auxiliary_balances(records, [config("1122"), config()], [root, account()], subject())
        self.assertEqual([row.account_code for row in rows], ["112201"])
        self.assertEqual(next(item for item in checks if item["account_code"] == "1122")["status"], "matched")

    def test_missing_account_coverage_has_a_primary_gap(self):
        root = account("1122", "应收账款", "应收账款", opening="150", ending="150", level=1)
        records = [party(1), party(2)]
        _, checks = dxn._auxiliary_balances(records, [config()], [root, account()], subject())
        check = next(item for item in checks if item["account_code"] == "1122")
        self.assertEqual(check["status"], "difference")
        self.assertEqual(check["ending_difference"], Decimal(-50))

    def test_opposite_direction_and_negative_balances_keep_their_sign(self):
        records = [party(1, qcs="120", qms="120"), party(2, qcs="120", qms="120"),
                   party(1, identifier="SYN-B", name="合成客商乙", jdfx="贷", qcs="20", qms="20"),
                   party(2, identifier="SYN-B", name="合成客商乙", jdfx="贷", qcs="20", qms="20")]
        rows, checks = dxn._auxiliary_balances(records, [config()], [account()], subject())
        self.assertEqual(rows[1].direction, "贷")
        self.assertEqual(checks[0]["status"], "matched")

    def test_missing_month_does_not_invent_opening_or_cumulative_occurrence(self):
        rows, checks = dxn._auxiliary_balances([party(2)], [config()], [account()], subject())
        self.assertIsNone(rows[0].opening)
        self.assertIsNone(rows[0].debit)
        self.assertEqual(rows[0].ending, Decimal(100))
        self.assertIsNone(checks[0]["opening_difference"])
        self.assertEqual(checks[0]["status"], "missing")

    def test_duplicate_party_month_or_changed_name_is_rejected(self):
        for records in ([party(1), party(1)], [party(1), party(2, name="合成不同名称")]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                dxn._auxiliary_balances(records, [config()], [account()], subject())


class SourceFingerprintTests(unittest.TestCase):
    def test_snapshot_hash_detects_same_size_same_timestamp_content_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "data"
            schema = data / "powersj"
            schema.mkdir(parents=True)
            source = schema / "c_kmyek.MYD"
            source.write_bytes(b"synthetic-one")
            with patch.object(dxn, "_open_source", side_effect=lambda path: path.open("rb")):
                fingerprint = dxn._snapshot(data, root / "copy", ("c_kmyek",))
                paper = PaperData(source_path=str(data), source_tables=("c_kmyek",), source_fingerprint=fingerprint)
                self.assertTrue(dxn.validate_paper_data_source(paper))
                stamp = source.stat()
                source.write_bytes(b"synthetic-two")
                os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                self.assertFalse(dxn.validate_paper_data_source(paper))
                self.assertEqual((root / "copy/powersj/c_kmyek.MYD").read_bytes(), b"synthetic-one")

    def test_source_member_change_and_missing_fingerprint_are_not_fresh(self):
        self.assertFalse(dxn.validate_paper_data_source(PaperData()))
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            schema = data / "powersj"
            schema.mkdir(parents=True)
            (schema / "c_kmyek.MYD").write_bytes(b"synthetic")
            with patch.object(dxn, "_open_source", side_effect=lambda path: path.open("rb")):
                fingerprint = dxn._snapshot(data, Path(folder) / "copy", ("c_kmyek",))
                paper = PaperData(source_path=str(data), source_tables=("c_kmyek",), source_fingerprint=fingerprint)
                (schema / "c_kmyek#part.MYD").write_bytes(b"new-member")
                self.assertFalse(dxn.validate_paper_data_source(paper))

    def test_new_auxiliary_source_invalidates_a_previously_missing_dimension(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            schema = data / "powersj"
            schema.mkdir(parents=True)
            (schema / "c_kmyek.MYD").write_bytes(b"synthetic")
            with patch.object(dxn, "_open_source", side_effect=lambda path: path.open("rb")):
                fingerprint = dxn._snapshot(data, Path(folder) / "copy", ("c_kmyek",))
                paper = PaperData(source_path=str(data), source_tables=("c_kmyek",), source_fingerprint=fingerprint,
                                  source_missing_tables=("c_hsxm_yye",))
                self.assertTrue(dxn.validate_paper_data_source(paper))
                (schema / "c_hsxm_yye.MYD").write_bytes(b"new-auxiliary")
                self.assertFalse(dxn.validate_paper_data_source(paper))


class PaperReaderTests(unittest.TestCase):
    def database(self, tables, rows):
        database = object.__new__(dxn.DxnLedgerDB)
        database.data = Path("synthetic/data")
        database.tables = tables
        database.connection = MagicMock()
        database.read = MagicMock(return_value=rows)
        database.connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [[], []]
        return database

    def test_missing_supported_journal_does_not_block_book_amounts(self):
        root = account("1122", "应收账款", "应收账款", level=1)
        database = self.database(dxn._TABLES, [root])
        result = database.paper_data(subject(), ["1122"], with_journal=True)
        self.assertEqual(result.rows[0].ending, Decimal(100))
        self.assertIsNone(result.journal)
        self.assertTrue(any("序时账" in note for note in result.warnings))

    def test_confirmed_reserve_component_reads_only_its_branch_after_full_root_check(self):
        from tb_autofill import audit_index, papers
        receivable = account("1122", "应收账款", "应收账款", level=1)
        reserve = replace(receivable, code="1231", name="坏账准备", path="坏账准备", direction="贷",
                          opening=Decimal(20), ending=Decimal(20), debit=Decimal(0), credit=Decimal(0))
        first = replace(reserve, code="123101", name="应收账款坏账准备", level=2,
                        path="坏账准备/应收账款坏账准备", opening=Decimal(12), ending=Decimal(12))
        second = replace(first, code="123102", name="其他应收款坏账准备",
                         path="坏账准备/其他应收款坏账准备", opening=Decimal(8), ending=Decimal(8))
        rows = [receivable, reserve, first, second]
        paper = audit_index.resolve("1122", "应收账款")[0]
        accounts, scoped = papers.report_scope(rows, paper)
        database = self.database(dxn._TABLES, rows)
        result = database.paper_data(subject(), [row.code for row in accounts], with_journal=False)
        self.assertEqual({row.code for row in result.rows}, {row.code for row in scoped})
        sql, params = database.connection.cursor.return_value.__enter__.return_value.execute.call_args_list[0].args
        self.assertEqual(params[-2:], ("1122%", "1231%"))
        self.assertNotIn(second, result.rows)
        database.read.return_value = [receivable, replace(reserve, ending=Decimal(21)), first, second]
        with self.assertRaisesRegex(ValueError, "子类与总科目不一致"):
            database.paper_data(subject(), [receivable.code, first.code], with_journal=False)

    def test_unconfirmed_or_orphan_secondary_is_not_a_reader_scope(self):
        child = account()
        for rows in ([child], [account("1122", "应收账款", "应收账款", level=1), child]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "备抵类别"):
                self.database(dxn._TABLES, rows).paper_data(subject(), [child.code], with_journal=False)

    def test_combined_journal_is_limited_without_claiming_full_coverage(self):
        roots = [account("1001", "库存现金", "库存现金", level=1),
                 account("1002", "银行存款", "银行存款", level=1)]
        database = self.database((*dxn._TABLES, dxn._JOURNAL_TABLE), roots)
        rows = [JournalRows([JournalRow(1, "2026-01-01", "合成-1", "1", "合成", "1001")]),
                JournalRows([JournalRow(2, "2026-02-01", "合成-2", "1", "合成", "1002")])]
        database.journal = MagicMock(side_effect=rows)
        with patch.object(dxn, "JOURNAL_LIMIT", 1):
            result = database.paper_data(subject(), ["1001", "1002"], with_journal=True)
        self.assertEqual(len(result.journal), 1)
        self.assertEqual(result.journal[0].code, "1002")
        self.assertFalse(result.journal.complete)

    def test_overlapping_primary_prefixes_do_not_duplicate_journal_lines(self):
        roots = [account("10", "合成一级甲", "合成一级甲", level=1),
                 account("1001", "合成一级乙", "合成一级乙", level=1)]
        database = self.database((*dxn._TABLES, dxn._JOURNAL_TABLE), roots)
        database.journal = MagicMock()
        result = database.paper_data(subject(), ["10", "1001"], with_journal=True)
        self.assertIsNone(result.journal)
        database.journal.assert_not_called()
        self.assertTrue(any("重叠" in note for note in result.warnings))

    def test_paper_reads_scope_with_company_dataset_and_prior_year_bound(self):
        database = object.__new__(dxn.DxnLedgerDB)
        database.data = Path("synthetic/data")
        database.tables = (*dxn._TABLES, *dxn._AUXILIARY_TABLES)
        database.connection = MagicMock()
        root = account("1122", "应收账款", "应收账款", level=1)
        database.read = MagicMock(return_value=[root, account(), account("5001", "开发成本", "开发成本", level=1)])
        cursor = database.connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[], [], [config()], [party(1), party(2)]]
        data = database.paper_data(subject(), ["1122"], with_journal=False)
        self.assertEqual({row.code for row in data.rows}, {"1122", "112201"})
        self.assertEqual(data.codes, ("1122",))
        self.assertEqual(len(data.auxiliary), 1)
        for call in cursor.execute.call_args_list:
            sql, params = call.args
            self.assertIn("dwid=%s AND ndid=%s", sql)
            self.assertEqual(params[:2], (2, 18))
        previous_sql, previous_params = cursor.execute.call_args_list[0].args
        self.assertIn("kjyear=%s", previous_sql)
        self.assertEqual(previous_params, (2, 18, 2025, 2, "1122%"))
        self.assertIsNone(data.journal)

    def test_read_wrapper_uses_one_snapshot_and_only_supported_optional_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            schema = data / "powersj"
            schema.mkdir(parents=True)
            for name in ("c_hsxm_yye.MYD", "c_kmvshsxm.MYD", "c_cwpz.frm"):
                (schema / name).write_bytes(b"synthetic")
            with patch.object(dxn, "_data_dir", return_value=data), patch.object(dxn, "DxnLedgerDB") as factory:
                database = factory.return_value.__enter__.return_value
                database.paper_data.return_value = PaperData()
                dxn.read_paper_data(subject(), ["1122"], "synthetic-install", with_journal=True)
                factory.assert_called_once()
                self.assertEqual(factory.call_args.kwargs["tables"], [*dxn._TABLES, *dxn._AUXILIARY_TABLES])
                database.paper_data.assert_called_once_with(subject(), ["1122"], with_journal=True)


if __name__ == "__main__":
    unittest.main()

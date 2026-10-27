"""Optional real Excel integration check. Creates only synthetic temporary files."""

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from openpyxl import load_workbook
from test_fields import ledger
from tb_autofill import fields, matching, workbook
from tb_autofill.models import ledger_digest


def main():
    template = ROOT / "tests" / "fixtures" / "试算平衡表-合成.xlsx"
    original_digest = fields.file_digest(template)
    rows = ledger()
    scan = fields.scan_template(template, ("opening", "ending"), "元")
    scan.source_digest = ledger_digest(rows)
    results = matching.match_fields(scan.fields, rows)
    with tempfile.TemporaryDirectory(prefix="deepaudit_excel_check_") as folder:
        output = Path(folder) / "synthetic-output.xlsx"
        workbook.write_results(scan, results, output, rows=rows)
        book = load_workbook(output, data_only=False)
        sheet = book["试算平衡表"]
        if sheet["D4"].value != 12000 or sheet["D14"].value != 700000 or sheet["D15"].value != "=SUM(D4:D14)":
            raise RuntimeError("金额、已有值或公式保护失败")
        book.close()
        if fields.file_digest(template) != original_digest:
            raise RuntimeError("原始模板被修改")
        print(f"Excel integration passed: {sum(r.selected for r in results)} synthetic cells; original unchanged")


if __name__ == "__main__":
    main()

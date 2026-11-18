r"""在 Windows 项目环境运行：.venv\Scripts\python -B tools/qa/probe_revenue_cutoff.py

使用内存 SQLite 代替鼎信诺副本、数据库进程及连接。目录定位替换为固定的虚拟路径，
游标仅转换参数占位符和结果字典；取数 SQL、JournalRow 生成和截止性筛选均使用产品实现。
不读取账套或连接模型。缺失和误标为诊断输出，不控制进程退出码。
"""
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tb_autofill import dxn, sampling
from tb_autofill.models import Subject


ROWS = (
    ("年末正常", 2025, 12, "2025-12-31"),
    ("次年一月正常", 2026, 1, "2026-01-03"),
    ("十二月错记次年日期", 2025, 12, "2026-01-03"),
    ("十二月无效日期", 2025, 12, "bad-date"),
    ("十一月缺日期", 2025, 11, None),
    ("十二月窗口外", 2025, 12, "2025-12-12"),
)


class SQLiteConnection:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def cursor(self):
        with closing(self.db.cursor()) as cursor:
            class Cursor:
                def execute(self, query, parameters):
                    cursor.execute(query.replace("%s", "?"), parameters)

                def fetchall(self):
                    return [dict(row) for row in cursor.fetchall()]

            yield Cursor()


def show_candidates(title, rows):
    picks = sampling.cutoff(rows, 2025, 12)
    print(f"\n{title}，候选 {len(picks)} 条")
    for row, reason in picks:
        print(f"  {row.summary} | 记账月份 {row.month} | 凭证日期 {row.date!r} | {reason}")
    return {row.summary: reason for row, reason in picks}


def main():
    subject = Subject("1", "合成收入测试公司", 2025, 12, "synthetic-project", ledger_id="1")
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE c_cwpz (
            dwid INTEGER, ndid INTEGER, kjyear INTEGER, kjmonth INTEGER, jzsj TEXT,
            pzzl TEXT, pzbh INTEGER, bh INTEGER, sm TEXT, kmbh TEXT,
            jffse NUMERIC, dffse NUMERIC, dfkmbh TEXT, dfkmmc TEXT)""")
        db.executemany("""INSERT INTO c_cwpz VALUES (1,1,?,?,?,'记',?,1,?,'6001',0,100,'','')""",
                       [(year, month, posted, i, label)
                        for i, (label, year, month, posted) in enumerate(ROWS, 1)])

        class ProbeLedger(dxn.DxnLedgerDB):
            def __init__(self, project_path, *args, **kwargs):
                self.data = project_path
                self.connection = SQLiteConnection(db)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

        with patch.object(dxn, "_data_dir", side_effect=lambda path: path), patch.object(dxn, "DxnLedgerDB", ProbeLedger):
            current = dxn.read_journals(subject, ["6001"])["6001"]
            following = dxn.read_journals(replace(subject, year=2026, month=1), ["6001"])["6001"]

    print("数据源为临时内存 SQLite；真实执行 read_journals → journal SQL → sampling.cutoff。")
    for label, rows in (("当年十二月查询", current), ("次年一月独立查询", following)):
        print(f"{label}：{len(rows)} 条，complete={rows.complete}，{[r.summary for r in rows]}")
    missing = sorted({r.summary for r in following} - {r.summary for r in current})
    print("当年查询缺少的次年来源记录：", missing)
    show_candidates("只使用当年查询", current)
    # 按测试凭证唯一的摘要去重，兼容未来当年查询已带回部分期后凭证的情况。
    combined = {r.summary: r for r in [*current, *following]}
    reasons = show_candidates("人工合并两个查询，仅诊断筛选逻辑", combined.values())
    label = "次年一月正常"
    reason = reasons.get(label, "")
    print("正常次年分录入选：", label in reasons)
    print("正常次年分录理由含“记账月份不一致”：", "记账月份不一致" in reason)


if __name__ == "__main__":
    main()

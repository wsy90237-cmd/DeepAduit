"""Offline pilot: preview or write a TB from two explicitly normalized CSV exports."""

import argparse
import calendar
from collections import defaultdict
import csv
from datetime import date
from decimal import Decimal
import hashlib
from io import StringIO
from pathlib import Path
import re

from tb_autofill import fields, matching, workbook
from tb_autofill.models import LedgerRow, ledger_digest

IDENTITY = ('主体', '账套', '截止月', '币种')
BALANCE = IDENTITY + ('科目编码', '父级编码', '科目名称', '科目方向', '科目类别', '年初余额',
                      '本年借方发生额', '本年贷方发生额', '期末余额')
JOURNAL = IDENTITY + ('凭证日期', '凭证号', '分录号', '科目编码', '借方金额', '贷方金额')
ZERO = Decimal(0)


def _records(content, headers):
    reader = csv.DictReader(StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
    columns = reader.fieldnames or []
    if len(set(columns)) != len(columns) or not set(headers).issubset(columns):
        raise ValueError('CSV 缺少必要列或列名重复：' + '、'.join(headers))
    for record in reader:
        if None in record or any(value is None for value in record.values()):
            raise ValueError(f'CSV 第 {reader.line_num} 行列数异常。')
        yield reader.line_num, {key: record[key].strip() for key in headers}


def _amount(text):
    if not re.fullmatch(r'-?[0-9]{1,12}(?:\.[0-9]{1,2})?', text):
        raise ValueError('金额必须显式填写为元，最多十二位整数及两位小数；缺失不能视为零。')
    return Decimal(text)


def read_rows(balance, journal):
    rows, signed, parents, declared_parents, context = {}, {}, {}, {}, None
    for line, record in _records(balance, BALANCE):
        identity = tuple(record[key] for key in IDENTITY)
        entity, ledger, period, currency = identity
        if not entity or not ledger or not re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', period) or currency != 'CNY':
            raise ValueError(f'余额表第 {line} 行缺少主体、账套或有效截止月，或币种不是 CNY。')
        if context is not None and identity != context:
            raise ValueError('余额表混有不同主体、账套、截止月或币种。')
        context = identity
        year, month = map(int, period.split('-'))
        if not 1900 <= year <= 2200:
            raise ValueError('截止年份需在 1900–2200。')
        code, parent, name = (record[key] for key in ('科目编码', '父级编码', '科目名称'))
        direction, kind = record['科目方向'], record['科目类别']
        if (not re.fullmatch(r'[0-9]+(?:[.\-][0-9]+)*', code) or code in rows or not name or '/' in name
                or direction not in {'借', '贷'} or kind not in {'资产负债', '损益'}):
            raise ValueError(f'余额表第 {line} 行科目编码、名称、方向或类别不明确。')
        if parent and (parent not in rows or not code.startswith(parent) or len(code) <= len(parent)):
            raise ValueError(f'余额表第 {line} 行父级编码不符合层级或未先出现。')
        opening, debit, credit, ending = (_amount(record[key]) for key in BALANCE[-4:])
        if opening + debit - credit != ending:
            raise ValueError(f'科目 {code} 年初余额加借减贷不等于期末余额。')
        sign = 1 if direction == '借' else -1
        ancestor = rows.get(parent)
        rows[code] = LedgerRow(code, name, f'{ancestor.path}/{name}' if ancestor else name, direction,
                               opening * sign, ending * sign, debit, credit, ancestor.level + 1 if ancestor else 1,
                               subject_id=entity, year=year, month=month, ledger_id=ledger,
                               is_profit_loss=kind == '损益')
        signed[code] = opening, debit, credit, ending
        declared_parents[code] = parent
        if parent:
            parents.setdefault(parent, []).append(code)
    if not rows:
        raise ValueError('余额表没有数据。')
    for code, parent in declared_parents.items():
        prefix_parent = next((code[:size] for size in range(len(code) - 1, 0, -1) if code[:size] in rows), '')
        if parent != prefix_parent:
            raise ValueError(f'科目 {code} 的父级与编码层级不一致。')
    for code, children in parents.items():
        if signed[code] != tuple(sum(values) for values in zip(*(signed[child] for child in children))):
            raise ValueError(f'父级科目 {code} 与直接子级合计不符。')
    root_opening = sum(signed[code][0] for code, parent in declared_parents.items() if not parent)
    if root_opening:
        raise ValueError(f'一级科目年初余额借贷不平，差额 {root_opening:+.2f}；请导入全账套。')

    end = date(year, month, calendar.monthrange(year, month)[1])
    turnover = defaultdict(lambda: [ZERO, ZERO])
    vouchers = defaultdict(lambda: ZERO)
    seen = set()
    for line, record in _records(journal, JOURNAL):
        if tuple(record[key] for key in IDENTITY) != context:
            raise ValueError(f'序时账第 {line} 行与余额表主体、账套、截止月或币种不一致。')
        day = date.fromisoformat(record['凭证日期'])
        voucher, entry, code = (record[key] for key in ('凭证号', '分录号', '科目编码'))
        key = day, voucher, entry
        if (record['凭证日期'] != day.isoformat() or not date(year, 1, 1) <= day <= end or not voucher
                or not entry or key in seen or code not in rows or code in parents):
            raise ValueError(f'序时账第 {line} 行超出期间、分录重复或未定位到末级科目。')
        seen.add(key)
        debit, credit = (_amount(record[header]) for header in JOURNAL[-2:])
        if bool(debit) == bool(credit):
            raise ValueError(f'序时账第 {line} 行必须且只能在一个方向有金额。')
        vouchers[(day, voucher)] += debit - credit
        turnover[code][0] += debit
        turnover[code][1] += credit
    if not seen:
        raise ValueError('没有序时账分录，无法验证来源。')
    if any(vouchers.values()):
        raise ValueError('序时账存在借贷不平的凭证。')
    gaps = [f'{code}：借 {turnover[code][0] - row.debit:+.2f} / 贷 {turnover[code][1] - row.credit:+.2f}'
            for code, row in rows.items() if code not in parents and turnover[code] != [row.debit, row.credit]]
    if gaps:
        raise ValueError('末级发生额不符（序时账减余额表）：' + '；'.join(gaps))
    return list(rows.values()), context


def run(balance, journal, template, report, output=None):
    sources = [Path(balance), Path(journal)]
    contents = [source.read_bytes() for source in sources]
    rows, context = read_rows(*contents)
    scan = fields.scan_template(template, ('opening', 'ending'))
    scan.source_digest = ledger_digest(rows)
    results = matching.match_fields(scan.fields, rows)
    for result in results:
        target = result.target
        if result.status == 'matched' and (target.statement == 'profit_loss' or target.measure == 'profit_loss'
                                           or any(row.is_profit_loss for row in result.sources)):
            result.status, result.amount = 'missing', None
            result.reason = '本试验未确认损益取数和结转排除口径，不自动填列。'
        result.selected = (target.basis == 'book' and not target.is_formula and target.value in (None, '')
                           and result.status == 'matched' and result.amount is not None)
    workbook.planned_writes(scan, results, rows=rows, allow_empty=output is None)
    digests = [hashlib.sha256(content).hexdigest() for content in contents]
    if [fields.file_digest(source) for source in sources] != digests:
        raise ValueError('导入期间源文件已变化，请重新导出并读取。')
    workbook.export_report(report, results, subject=' / '.join(context), unit=scan.unit,
                           warnings=[*scan.warnings, '余额表 SHA-256：' + digests[0], '序时账 SHA-256：' + digests[1]])
    if output is not None:
        workbook.write_results(scan, results, output, rows=rows, preserve_tb_balance=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('balance', type=Path)
    parser.add_argument('journal', type=Path)
    parser.add_argument('template', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, help='可选。另存 TB 草稿，需要 Windows Excel。')
    args = parser.parse_args()
    try:
        results = run(args.balance, args.journal, args.template, args.report, args.output)
    except (ValueError, OSError, csv.Error) as exc:
        parser.exit(1, f'{exc}\n')
    print(f'匹配字段 {len(results)} 个；可填空白账面字段 {sum(item.selected for item in results)} 个。')


if __name__ == '__main__':
    main()

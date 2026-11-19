"""Annual local intake and cash workpaper generation."""
import calendar
import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from . import audit_index, dxn_paper, papers, paper_pack
from .bank_evidence import BankBalance, reconcile_bank_balance
from .bank_listing import read_listing
from .pdf_text import extract_pdf_pages


def folders(store, rows):
    active = {row.code for row in rows if any((row.opening, row.ending, row.debit, row.credit))}
    result = {store.inbox: '', store.root / '00 待分类资料': ''}
    for row in rows:
        if row.level != 1 or not any(code.startswith(row.code) for code in active):
            continue
        label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', f'{row.code} {row.name}')[:120].rstrip(' .')
        directory = store.root / label
        if not row.code.strip() or not label or directory in result and result[directory] != row.code:
            raise ValueError('一级科目目录名为空或发生冲突，请核对账套。')
        result[directory] = row.code
    for path in result:
        if path.resolve().parent != store.root.resolve():
            raise ValueError('资料目录指向项目以外，已停止。')
        path.mkdir(exist_ok=True)
    return result


def inventory(directories):
    entries = {}
    for directory, code in directories.items():
        if directory.resolve() != directory:
            raise ValueError('资料目录指向其他位置')
        for path in directory.iterdir():
            if path.name.startswith(('~$', '.')) or path.suffix.lower() in {'.tmp', '.part', '.crdownload'}:
                continue
            if path.resolve() == path and path.is_file():
                stat = path.stat()
                entries[path] = (code, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return entries


def parse_page(text, subject, cutoff):
    if not text.strip():
        raise ValueError('此页没有可读取的 PDF 文字层')
    def field(labels, optional=False):
        values = re.findall(r'^(?:' + labels + r')[ \t]*[：:][ \t]*([^\r\n]+)$', text, re.M)
        if optional and not values:
            return ''
        if len(values) != 1:
            raise ValueError('缺少或重复字段：' + labels)
        return values[0].strip()
    kinds = [kind for label, kind in [('回函', 'confirmation'), ('对账单', 'statement')] if label in text]
    if len(kinds) != 1:
        raise ValueError('不能唯一确定回函或对账单')
    name = field('户名|账户名称|银行账户户名')
    entity = field('被审计单位|主体', optional=True)
    day = field('截止日|截止日期|余额截止日')
    amount = field('回函余额|期末余额|账户余额|银行余额' if kinds[0] == 'confirmation' else '期末余额|对账单余额')
    unit = field('金额单位|单位', optional=True)
    if amount.endswith('元'):
        unit = unit or '元'
        amount = amount[:-1].strip()
    if (entity and entity != subject.name) or name != subject.name or day != cutoff.isoformat() or unit != '元':
        raise ValueError('主体、截止日或金额单位不符')
    currency = field('币种')
    if currency in ('人民币（CNY）', '人民币(CNY)'):
        currency = 'CNY'
    return kinds[0], BankBalance(name, field('完整账号|账号|银行账号'), currency, day, amount)


def snapshot(store, source):
    folder = store.root / '自动收件快照'
    if folder.resolve() != folder:
        raise ValueError('证据目录不能重定向。')
    folder.mkdir(exist_ok=True)
    temp = None
    try:
        digest = hashlib.sha256()
        with source.open('rb') as incoming, tempfile.NamedTemporaryFile(dir=folder, delete=False) as saved:
            temp = Path(saved.name)
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                saved.write(chunk)
        key = digest.hexdigest()
        with source.open('rb') as incoming:
            if hashlib.file_digest(incoming, 'sha256').hexdigest() != key:
                raise ValueError('投放文件正在变更，请等待复制完成。')
        target = folder / (key + source.suffix.lower())
        if target.resolve() != target:
            raise ValueError('证据快照不能重定向。')
        if not target.exists():
            try:
                os.rename(temp, target)
            except FileExistsError:
                pass
        with target.open('rb') as saved:
            if hashlib.file_digest(saved, 'sha256').hexdigest() != key:
                raise ValueError('证据快照指纹不一致，请复核。')
        return key, target
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def prepare(store, entries, subject, rows, currency, library, source, extractor=extract_pdf_pages):
    cutoff = date(subject.year, subject.month, calendar.monthrange(subject.year, subject.month)[1])
    banks = [row for row in rows if row.level == 1 and row.name == '银行存款']
    if len(banks) != 1 or banks[0].ending is None:
        return None, ['没有唯一的银行存款总账期末余额，资料仅归档。']
    bank = banks[0]
    paper = audit_index.resolve(bank.code, bank.name, library)[0]
    if not paper or paper.name != '货币资金':
        return None, ['银行存款尚未对应货币资金底稿。']
    accounts, scoped = papers.report_scope(rows, paper, library)
    listings, pages, records, issues, seen = [], [], [], [], set()
    with store._connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS intake_origin(digest TEXT PRIMARY KEY, path TEXT)')
        known = {row[0] for row in db.execute('SELECT path FROM intake_origin')}
    ordered = sorted(entries, key=lambda path: (str(path.relative_to(store.root)) not in known, path))
    for path in ordered:
        code = entries[path][0]
        label = str(path.relative_to(store.root))
        suffix = path.suffix.lower()
        if code not in ('', bank.code) or suffix not in {'.xlsx', '.pdf'}:
            issues.append(label + '：已归档，非银行自动填写范围')
            continue
        digest, copy = snapshot(store, path)
        if (digest, suffix) in seen:
            continue
        seen.add((digest, suffix))
        try:
            with store._connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS intake_text(digest TEXT PRIMARY KEY, pages TEXT NOT NULL)')
                db.execute('INSERT OR REPLACE INTO intake_origin VALUES (?,?)', (digest, label))
            if suffix == '.xlsx':
                listing = read_listing(copy.read_bytes(), entity=subject.name, cutoff=cutoff, code=bank.code,
                                       currency=currency, ledger_balance=bank.ending)
                listings.append((label, listing, digest))
            else:
                with store._connect() as db:
                    cached = db.execute('SELECT pages FROM intake_text WHERE digest=?', (digest,)).fetchone()
                if cached is None:
                    extracted = list(extractor(copy))
                    with store._connect() as db:
                        db.execute('INSERT OR IGNORE INTO intake_text VALUES (?,?)', (digest, json.dumps(extracted)))
                else:
                    extracted = json.loads(cached[0])
                parsed = [(number, *parse_page(text.replace('\r\n', '\n'), subject, cutoff), text)
                          for number, text in extracted]
                if not parsed:
                    raise ValueError('未提取到有效页')
                pages.extend((label, digest, *item) for item in parsed)
        except (ValueError, OSError, RuntimeError) as exc:
            issues.append(f'{label}：待核，{exc}')
            if suffix == '.xlsx':
                listings.append((label, [], digest))
    lookup = {}
    if len(listings) == 1:
        label, listing, digest = listings[0]
        for item in listing:
            lookup[item.balance.account_number] = item.balance
            records.append(dict(field='逐户余额清单', value=str(item.balance.balance), quote=item.balance.account_number,
                                filename=label, ref=f'银行账户余额清单!H{item.row}', sha256=digest, risk='逐户合计已与总账核平，非审定数'))
    elif listings:
        issues.append('多份不同余额清单，无法确定采用哪份，请移出旧版。')
    comparable = set()
    for label, digest, number, kind, evidence, text in pages:
        book = lookup.get(evidence.account_number.replace(' ', ''))
        if not book and not listings:
            details = [row for row in scoped if row.level > 1 and row.code.startswith(bank.code) and row.ending is not None
                       and not any(other.code != row.code and other.code.startswith(row.code) for other in scoped)
                       and evidence.account_number in re.findall(r'(?<!\d)\d+(?!\d)', row.name)]
            if len(details) == 1:
                book = BankBalance(subject.name, evidence.account_number, currency, cutoff, details[0].ending)
        result = reconcile_bank_balance(book or BankBalance(), evidence, evidence_kind=kind)
        if result.difference is None:
            issues.append(f'{label} 第{number}页：待核，缺少可比逐户余额或口径不一致')
            continue
        if result.difference:
            issues.append(f'{label} 第{number}页：差异待核 {result.difference:.2f}')
        comparable.add((book.account_number, kind))
        records.append(dict(field='银行回函余额' if kind == 'confirmation' else '银行对账单余额', value=str(evidence.balance),
                            quote=text[:32000], comparison=f'账面 {book.balance:.2f}；银行减账面 {result.difference:.2f}',
                            risk='差异待核' if result.difference else '金额一致，非审计结论', next_step=result.recommendation,
                            filename=label, page=number, sha256=digest, ref=f'{digest}:{number}'))
    if not lookup:
        issues.append('请提供或复核逐户余额清单；目前无法检查清单内各户的资料覆盖情况。')
    for account in lookup:
        missing = [name for kind, name in [('confirmation', '银行回函'), ('statement', '银行对账单')]
                   if (account, kind) not in comparable]
        if missing:
            issues.append(f'{listings[0][0]} · 账号 {account}：未匹配可比的{"、".join(missing)}；请核对原件、适用程序或替代证据。')
    records.sort(key=lambda item: (item['field'] != '逐户余额清单', item['filename'], item.get('page', 0), item['ref']))
    signature = hashlib.sha256(json.dumps([source, currency, records], ensure_ascii=False).encode()).hexdigest()
    return (signature, paper, accounts, scoped, records), issues


def render(path, prepared, subject, library, data=None, reviewed=()):
    _, paper, accounts, rows, records = prepared
    if data:
        accounts, rows = papers.report_scope(data.rows, paper, library)
    if not records:
        records = [dict(field='自动收件状态', risk='当前没有可采用的银行资料；历史版本的自动核对结果不再适用。')]
    dxn_paper.build(path, paper=paper, subject_label=f'{subject.name} / {subject.year}-{subject.month:02d}',
                    rows=rows, accounts=accounts, resolve=lambda code, name: audit_index.resolve(code, name, library)[0],
                    procedures=paper_pack.procedures(paper.index_no, paper.name), evidence_rows=[*reviewed, *records],
                    paper_data=data, **({'journal': data.journal} if data else {}))


def last_export(store):
    with store._connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS auto_exports(version INTEGER PRIMARY KEY, signature TEXT)')
        return db.execute('SELECT version,signature FROM auto_exports ORDER BY version DESC LIMIT 1').fetchone()


def publish(store, temp, signature):
    folder = store.root / '底稿草稿' / '货币资金'
    if folder.resolve() != folder:
        raise ValueError('底稿输出目录不能重定向。')
    folder.mkdir(parents=True, exist_ok=True)
    last_export(store)
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        last = db.execute('SELECT version,signature FROM auto_exports ORDER BY version DESC LIMIT 1').fetchone()
        if last and last[1] == signature:
            return None
        version = last[0]+1 if last else 1
        while True:
            output = folder / f'货币资金－自动草稿－第{version:03d}版.xlsx'
            if not output.exists():
                os.rename(temp, output)
                break
            version += 1
        db.execute('INSERT INTO auto_exports VALUES (?,?)', (version, signature))
        return output

"""Poll only the active annual project; publish on the UI thread after revalidation."""
import hashlib
import json
from pathlib import Path
import re
from threading import Thread
from tkinter import simpledialog
from uuid import uuid4

from . import audit_index, auto_project as intake, dxn, evidence_draft as draft, papers


class Intake:
    def __init__(self, app, store):
        self.app, self.store = app, store
        self.subject, self.rows = app.loaded_subject, app.rows
        self.library = tuple(app.paper_library)
        self.directories = intake.folders(store, self.rows)
        self.observed = self.finished = self.approval = None
        self.running = False
        self.notes = ['尚未扫描投放目录。']
        self.restore_attempt = None
        with store._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS intake_scope(id INTEGER PRIMARY KEY, source TEXT, currency TEXT, fingerprint TEXT)')

    def current(self):
        return self.app.intake is self and self.app.loaded_subject is self.subject and self.app.rows is self.rows

    def status(self, text):
        self.app.intake_status.set(text)

    def start(self):
        self.status(f'公司资料夹：{self.store.root}。确认范围后，投放资料会在本机自动识别。')
        self.app.after(1500, self.poll)

    def confirm(self, parent=None):
        if not self.current() or not self.app.scan or not self.app.template_var.get().strip():
            raise ValueError('请先读取账套并识别当前 TB 模板。')
        source = draft.source_key(self.app)
        paper = audit_index.find(self.library, '1-010')
        accounts, scoped = papers.report_scope(self.rows, paper, self.library)
        if not any(row.name == '银行存款' for row in accounts):
            raise ValueError('当前账套没有可用于货币资金自动底稿的银行存款科目。')
        summary = '、'.join(f'{row.code} {row.name}' for row in accounts)
        currency = simpledialog.askstring('确认自动底稿范围', f'{self.subject.label}\nTB：{self.app.template_var.get()}\n'
            f'货币资金整册：{summary}\n确认以上来源和映射后，输入账套记账本位币（三位代码，如 CNY）：', parent=parent or self.app)
        if currency is None:
            return
        currency = currency.strip().upper()
        if not re.fullmatch('[A-Z]{3}', currency) or currency == 'XXX':
            raise ValueError('请填写有效的记账本位币。')
        self.verify_scope(source, currency, scoped, accounts)

    def verify_scope(self, source, currency, scoped, accounts, previous=None):
        install = self.app.install_var.get().strip()
        from .gui import PaperDialog
        def ready(data):
            if self.current() and source == draft.source_key(self.app):
                fingerprint = json.dumps([data.source_fingerprint, [row.code for row in accounts], data.source_missing_tables])
                if previous is not None and fingerprint != previous:
                    self.status('原账套已变化，旧底稿待复核；请重新确认自动底稿范围。')
                    return
                with self.store._connect() as db:
                    db.execute('INSERT OR REPLACE INTO intake_scope VALUES (1,?,?,?)', (source, currency, fingerprint))
                self.data = data
                self.confirmed_scan = self.app.scan
                self.review = draft.review_state(self.app)
                self.template_stat = self.stat_template()
                self.approval = (source, currency)
                self.finished = None
                self.status('已确认来源，正在检查资料。')
        self.app._async('核验当前账套与整册范围', lambda: PaperDialog._read_paper_data(
            self.subject, scoped, accounts, install, True), ready)

    def stat_template(self):
        stat = Path(self.app.template_var.get().strip()).stat()
        return stat.st_size, stat.st_mtime_ns

    def poll(self):
        if not self.current():
            return
        try:
            if not self.running and not self.app.busy:
                self.inspect()
        except Exception as exc:
            self.status(f'自动收件暂停：{exc}')
        finally:
            self.app.after(3000, self.poll)

    def inspect(self):
        entries = intake.inventory(self.directories)
        if entries != self.observed:
            self.observed = entries
            self.notes = ['检测到投放目录有变动，等待文件稳定；旧草稿不代表当前资料状态。']
            self.status(f'发现 {len(entries)} 个文件，等候复制稳定。已有底稿请以复核后的新状态为准。')
            return
        approval = self.approval
        if not approval and self.app.scan and self.restore_attempt is not self.app.scan:
            self.restore_attempt = self.app.scan
            with self.store._connect() as db:
                saved = db.execute('SELECT source,currency,fingerprint FROM intake_scope WHERE id=1').fetchone()
            if saved and draft.source_key(self.app) == saved[0]:
                accounts, scoped = papers.report_scope(self.rows, audit_index.find(self.library, '1-010'), self.library)
                self.verify_scope(saved[0], saved[1], scoped, accounts, saved[2])
                return
        if approval:
            try:
                valid = self.app.scan is self.confirmed_scan and all((stat.st_size, stat.st_mtime_ns) == (size, stamp)
                    for path, size, stamp, _ in self.data.source_fingerprint for stat in [Path(path).stat()])
                valid = valid and self.stat_template() == self.template_stat and self.review == draft.review_state(self.app)
            except (OSError, ValueError):
                valid = False
            if not valid:
                self.approval = None
                self.finished = None
                self.notes = ['账套或 TB 已变化，旧底稿待复核；请重新确认范围。']
                self.status('账套或 TB 已变化，旧底稿待复核；请重新确认自动底稿范围。')
                return
        if self.finished == (entries, approval):
            return
        if not approval:
            self.finished = (entries, None)
            self.notes = ['尚未确认主体、期间、TB 和币种，未读取投放资料。']
            self.status(f'已发现 {len(entries)} 个文件。请先确认范围，再本机识别和出稿。')
            return
        self.running = True
        self.status('正在本机整理资料并核对来源…')
        confirmed_data = self.data
        def work():
            temp, data, signature, error = None, None, None, None
            notes = []
            try:
                source, currency = approval
                prepared, notes = intake.prepare(self.store, entries, self.subject, self.rows, currency, self.library, source)
                last = intake.last_export(self.store)
                if prepared and (prepared[-1] or last):
                    signature, paper, accounts, scoped, _ = prepared
                    data = confirmed_data
                    reviewed = draft.confirmed_rows(self.store, paper.index_no, source)
                    signature = hashlib.sha256(json.dumps([signature, data.source_fingerprint, reviewed], default=str).encode()).hexdigest()
                    if not last or signature != last[1]:
                        temp = self.store.root / ('.' + uuid4().hex + '.xlsx')
                        intake.render(temp, prepared, self.subject, self.library, data, reviewed)
                        if not dxn.validate_paper_data_source(data):
                            raise ValueError('账套已变化，请重新读取并复核。')
            except Exception as exc:
                error = str(exc)
            self.app._post(lambda: finish(temp, signature, notes, error))
        def finish(temp, signature, notes, error):
            try:
                if not self.current() or approval != self.approval or entries != intake.inventory(self.directories):
                    return
                if not self.app.scan or draft.source_key(self.app) != approval[0]:
                    self.approval = None
                    raise ValueError('TB 已变化，请重新确认自动底稿范围。')
                if error:
                    self.approval = None
                    raise ValueError(error)
                output = intake.publish(self.store, temp, signature) if temp else None
                self.notes = notes or ['没有待核异常。']
                self.finished = (entries, approval)
                heading = f'已生成：{output.name}' if output else '未产生新版本'
                self.status(f'{heading}。已检查 {len(entries)} 个投放文件。' + (' '+notes[0] if notes else ''))
            except Exception as exc:
                self.notes = [str(exc)]
                self.status(f'自动底稿待复核：{exc}')
            finally:
                if temp:
                    temp.unlink(missing_ok=True)
                self.running = False
        Thread(target=work, daemon=True).start()

"""Poll only the active annual project and display local evidence checks."""
import json
from pathlib import Path
import re
from threading import Thread
from tkinter import simpledialog

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

    def agent_brief(self):
        if not self.current():
            return '项目已切换，请重新打开当前项目资料。'
        heading = f'{self.subject.label} · 银行存款核对（本机）\n'
        if not self.approval:
            return heading + '请先读取账套、在审计对话中复核 TB，再确认银行核对范围。'
        if self.running or self.finished != (intake.inventory(self.directories), self.approval):
            return heading + '资料正在等待扫描或重新核对，请稍后重新打开任务单。'
        return heading + '以下为最近一次本机核对的结果，不代表审计结论。\n\n' + '\n'.join(self.notes)

    def start(self):
        self.status(f'公司资料夹：{self.store.root}。放入资料后自动识别；确认银行核对范围后可查看核对结果。')
        self.app.after(1500, self.poll)

    def confirm(self):
        if not self.current() or not self.app.scan or not self.app.template_var.get().strip():
            raise ValueError('请先读取账套并识别当前 TB 模板。')
        source = draft.source_key(self.app)
        paper = audit_index.find(self.library, '1-010')
        accounts, scoped = papers.report_scope(self.rows, paper, self.library)
        summary = '、'.join(f'{row.code} {row.name}' for row in accounts)
        currency = simpledialog.askstring('确认银行核对范围', f'{self.subject.label}\nTB：{self.app.template_var.get()}\n'
            f'货币资金整册：{summary}\n确认以上来源和映射后，输入账套记账本位币（三位代码，如 CNY）：', parent=self.app)
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
                    self.status('原账套已变化，请重新确认银行核对范围。')
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
                self.status('账套或 TB 已变化，请重新确认银行核对范围。')
                return
        if self.finished == (entries, approval):
            return
        self.running = True
        self.status('正在本机整理资料并核对来源…')
        confirmed_data = self.data if approval else None

        def work():
            error = None
            notes = []
            try:
                source, currency = approval or ('', '')
                _, notes = intake.prepare(self.store, entries, self.subject, self.rows, currency, self.library, source)
                if confirmed_data is not None and not dxn.validate_paper_data_source(confirmed_data):
                    raise ValueError('账套已变化，请重新读取并复核。')
            except Exception as exc:
                error = str(exc)
            self.app._post(lambda: finish(notes, error))

        def finish(notes, error):
            try:
                if not self.current() or approval != self.approval or entries != intake.inventory(self.directories):
                    return
                if approval and (not self.app.scan or draft.source_key(self.app) != approval[0]):
                    self.approval = None
                    raise ValueError('TB 已变化，请重新确认银行核对范围。')
                if error:
                    self.approval = None
                    raise ValueError(error)
                self.notes = notes or ['没有待核异常。']
                self.finished = (entries, approval)
                heading = '银行资料已核对' if approval else '待确认银行核对范围'
                self.status(f'{heading} · 已扫描 {len(entries)} 个文件。' + (' '+notes[0] if notes else ''))
            except Exception as exc:
                self.notes = [str(exc)]
                self.status(f'银行资料待复核：{exc}')
            finally:
                self.running = False
        Thread(target=work, daemon=True).start()

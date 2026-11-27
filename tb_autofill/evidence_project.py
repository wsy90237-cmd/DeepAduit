"""本机年度项目资料：保留 PDF 快照、页证据和人工确认的建议。"""
from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
import sqlite3
import uuid
from pathlib import Path
from stat import S_ISDIR, S_ISREG


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class ProjectStore:
    @staticmethod
    def _project_path(root, entity, year):
        entity, year = str(entity).strip(), str(year).strip()
        if not entity or not year.isascii() or not year.isdigit() or len(year) != 4 or not 1900 <= int(year) <= 9999:
            raise ValueError('必须指定主体和四位有效年度')
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', entity).strip(' .')[:48].rstrip(' .')
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
        if not safe_name or safe_name.split('.')[0].upper() in reserved:
            safe_name = '项目_' + safe_name
        project_key = hashlib.sha256(entity.encode('utf-8')).hexdigest()[:16]
        return Path(root).resolve() / f'{safe_name}-{project_key}' / year

    @classmethod
    def open_existing(cls, root, entity, year):
        """打开已有项目；不存在时不创建任何目录或数据库。"""
        path = cls._project_path(root, entity, year)
        try:
            path.stat()
        except FileNotFoundError:
            return None
        return cls(root, entity, year, create=False)

    def __init__(self, root, entity, year, *, create=True):
        self.entity = str(entity).strip()
        self.year = str(year).strip()
        self.root = self._project_path(root, self.entity, self.year)
        self.files = self.root / 'pdf'
        self.inbox = self.root / '原始资料'
        self.db = self.root / 'projects.sqlite3'
        created = False
        if create:
            try:
                self.root.mkdir(parents=True)
            except FileExistsError:
                pass
            else:
                self.files.mkdir()
                created = True
        if not created:
            self.require_available()
        with self._connect(create=created) as db:
            if not created:
                for table in ('documents', 'pages', 'suggestions', 'suggestion_evidence'):
                    db.execute(f'SELECT * FROM {table} LIMIT 0')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, entity TEXT NOT NULL, year TEXT NOT NULL,
                    name TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    pdf_path TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(entity, year, name, version));
                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL,
                    PRIMARY KEY(document_id, page));
                CREATE TABLE IF NOT EXISTS text_layer_documents (document_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS suggestions (
                    id TEXT PRIMARY KEY, entity TEXT NOT NULL, year TEXT NOT NULL,
                    text TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS suggestion_evidence (
                    suggestion_id TEXT NOT NULL, document_id TEXT NOT NULL, page INTEGER NOT NULL,
                    PRIMARY KEY(suggestion_id, document_id, page));
                CREATE INDEX IF NOT EXISTS document_lookup ON documents(entity, year, name COLLATE CASEFOLD, version);
                CREATE TABLE IF NOT EXISTS bank_listings (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, name TEXT NOT NULL,
                    sha256 TEXT NOT NULL, content BLOB NOT NULL);
            ''')
        self.inbox.mkdir(exist_ok=True)

    def import_bank_listing(self, path, scope, validate):
        """Persist only a validated byte-for-byte copy; latest valid import owns the scope."""
        source = Path(path)
        if source.suffix.lower() != ".xlsx":
            raise ValueError("逐户余额清单须为 XLSX 文件。")
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if _digest(source) != digest:
            raise ValueError("清单来源正在变更，请关闭文件后重试。")
        accounts = validate(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT id, sha256, name, content FROM bank_listings WHERE scope=? ORDER BY rowid DESC LIMIT 1",
                                (scope,)).fetchone()
            if latest and latest["sha256"] == digest and latest["content"] == payload:
                return latest["id"], digest, latest["name"], accounts
            listing_id = uuid.uuid4().hex
            db.execute("INSERT INTO bank_listings VALUES (?, ?, ?, ?, ?)",
                       (listing_id, scope, source.name, digest, payload))
        return listing_id, digest, source.name, accounts

    def verify_bank_listing(self, listing_id):
        """Check integrity and return whether a snapshot is still the current scope version."""
        with self._connect() as db:
            row = db.execute(
                """SELECT b.*, b.rowid=(SELECT MAX(x.rowid) FROM bank_listings x WHERE x.scope=b.scope) AS current
                FROM bank_listings b WHERE id=?""", (listing_id,)).fetchone()
            return bool(row and row["current"] and hashlib.sha256(row["content"]).hexdigest() == row["sha256"])

    def current_bank_listing(self, scope):
        with self._connect() as db:
            row = db.execute("SELECT id, sha256, name, content FROM bank_listings WHERE scope=? ORDER BY rowid DESC LIMIT 1",
                             (scope,)).fetchone()
            if row and hashlib.sha256(row["content"]).hexdigest() == row["sha256"]:
                return tuple(row)
            return None

    def require_available(self):
        if not S_ISDIR(self.files.stat().st_mode):
            raise NotADirectoryError(self.files)
        if not S_ISREG(self.db.stat().st_mode):
            raise OSError(f'项目资料数据库路径无效：{self.db}')

    @contextmanager
    def _connect(self, *, create=False):
        uri = self.db.as_uri()
        if self.db.drive.startswith('\\\\'):
            uri = uri.replace('file://', 'file:////', 1)
        mode = 'rwc' if create else 'rw'
        db = sqlite3.connect(f'{uri}?mode={mode}', uri=True)
        db.row_factory = sqlite3.Row
        db.create_collation('CASEFOLD', lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_pdf(self, path, extractor):
        source = Path(path)
        if source.suffix.lower() != '.pdf':
            raise ValueError('只支持 PDF 文件')
        document_id = uuid.uuid4().hex
        snapshot = self.files / (document_id + '.pdf')
        try:
            before = _digest(source)
            latest_sql = ('SELECT *, id IN (SELECT document_id FROM text_layer_documents) AS text_layer '
                          'FROM documents WHERE entity=? AND year=? AND name=? COLLATE CASEFOLD '
                          'ORDER BY version DESC LIMIT 1')
            lookup = (self.entity, self.year, source.name)
            with self._connect() as db:
                prior = db.execute(latest_sql, lookup).fetchone()
                if prior and prior['sha256'] == before and prior['text_layer']:
                    self._verified_path(prior)
                    return self.get_document(prior['id'])
            with source.open('rb') as src, snapshot.open('xb') as dst:
                for block in iter(lambda: src.read(1024 * 1024), b''):
                    dst.write(block)
            if _digest(snapshot) != before or _digest(source) != before:
                raise ValueError('来源文件在复制期间发生变化，请重试')
            with snapshot.open('rb') as stream:
                if stream.read(5) != b'%PDF-':
                    raise ValueError('文件不是有效的 PDF')
            texts = list(extractor(snapshot))
            if not texts or any(not isinstance(item, str) for item in texts):
                raise ValueError('PDF 提取结果必须是非空的逐页文本列表')
            if _digest(snapshot) != before or _digest(source) != before:
                raise ValueError('识别期间 PDF 或来源文件发生变化，请重新导入')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute(latest_sql, lookup).fetchone()
                if existing and existing['sha256'] == before and existing['text_layer']:
                    self._verified_path(existing)
                    snapshot.unlink()
                    return self.get_document(existing['id'])
                if existing and (not prior or prior['id'] != existing['id']):
                    raise ValueError('同名资料已被另一项导入更新，请核对后重试')
                version = db.execute('SELECT COALESCE(MAX(version), 0) + 1 FROM documents WHERE entity=? AND year=? AND name=? COLLATE CASEFOLD',
                                     (self.entity, self.year, source.name)).fetchone()[0]
                db.execute('UPDATE documents SET reviewed=0 WHERE entity=? AND year=? AND name=? COLLATE CASEFOLD',
                           (self.entity, self.year, source.name))
                db.execute('INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, 0)',
                           (document_id, self.entity, self.year, source.name, version, before, snapshot.name))
                db.execute('INSERT INTO text_layer_documents VALUES (?)', (document_id,))
                db.executemany('INSERT INTO pages VALUES (?, ?, ?)',
                               ((document_id, number, text) for number, text in enumerate(texts, 1)))
        except BaseException:
            snapshot.unlink(missing_ok=True)
            raise
        return self.get_document(document_id)

    def list_documents(self):
        """返回轻量目录，不加载页面文字；旧版本也包含在内。"""
        with self._connect() as db:
            rows = db.execute('SELECT *, id IN (SELECT document_id FROM text_layer_documents) AS text_layer, version=(SELECT MAX(d2.version) FROM documents d2 WHERE d2.entity=d.entity AND d2.year=d.year AND d2.name=d.name COLLATE CASEFOLD) AS current FROM documents d WHERE entity=? AND year=? ORDER BY name, version', (self.entity, self.year)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item['pdf_path'] = str((self.files / row['pdf_path']).resolve())
                item['current'], item['reviewed'] = bool(item['current']), bool(item['reviewed'])
                result.append(item)
            return result

    def get_document(self, document_id):
        """按需加载选中版本的页文本和证据编号。"""
        with self._connect() as db:
            row = db.execute('SELECT *, id IN (SELECT document_id FROM text_layer_documents) AS text_layer, version=(SELECT MAX(d2.version) FROM documents d2 WHERE d2.entity=d.entity AND d2.year=d.year AND d2.name=d.name COLLATE CASEFOLD) AS current FROM documents d WHERE id=? AND entity=? AND year=?', (document_id, self.entity, self.year)).fetchone()
            if not row:
                raise ValueError('资料不属于当前项目')
            item = dict(row)
            item['pdf_path'] = str((self.files / row['pdf_path']).resolve())
            item['current'], item['reviewed'] = bool(item['current']), bool(item['reviewed'])
            item['pages'] = [{'page': p['page'], 'text': p['text'], 'evidence_id': f"{row['id']}:{p['page']}"}
                             for p in db.execute('SELECT page, text FROM pages WHERE document_id=? ORDER BY page', (document_id,))]
            return item

    def _verified_path(self, row):
        path = (self.files / row['pdf_path']).resolve()
        if path.parent != self.files.resolve() or not path.is_file() or _digest(path) != row['sha256']:
            raise ValueError('资料快照丢失、路径无效或指纹不一致')
        return path

    def verify_document(self, document_id):
        """校验并返回此项目中任意版本（含历史版本）的 PDF 快照路径。"""
        with self._connect() as db:
            row = db.execute('SELECT * FROM documents WHERE id=? AND entity=? AND year=?',
                             (document_id, self.entity, self.year)).fetchone()
            if not row:
                raise ValueError('资料不属于当前项目')
            return self._verified_path(row)

    def mark_reviewed(self, document_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM documents WHERE id=? AND entity=? AND year=? AND id IN (SELECT document_id FROM text_layer_documents) AND version=(SELECT MAX(d2.version) FROM documents d2 WHERE d2.entity=documents.entity AND d2.year=documents.year AND d2.name=documents.name COLLATE CASEFOLD)', (document_id, self.entity, self.year)).fetchone()
            if not row:
                raise ValueError('请使用当前版本的文字版 PDF 复核，旧版提取结果不能继续使用。')
            self._verified_path(row)
            db.execute('UPDATE documents SET reviewed=1 WHERE id=?', (document_id,))

    def add_suggestion(self, text, evidence_ids):
        if not isinstance(text, str) or not text.strip() or not evidence_ids:
            raise ValueError('建议内容和证据不能为空')
        ids = set(evidence_ids)
        suggestion_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            refs = []
            for evidence_id in ids:
                try:
                    document_id, page = evidence_id.split(':')
                    page = int(page)
                except (ValueError, AttributeError):
                    raise ValueError('证据编号无效') from None
                row = db.execute('SELECT d.* FROM documents d JOIN pages p ON p.document_id=d.id JOIN text_layer_documents t ON t.document_id=d.id WHERE d.id=? AND p.page=? AND d.entity=? AND d.year=? AND d.version=(SELECT MAX(d2.version) FROM documents d2 WHERE d2.entity=d.entity AND d2.year=d.year AND d2.name=d.name COLLATE CASEFOLD)', (document_id, page, self.entity, self.year)).fetchone()
                if not row:
                    raise ValueError('证据不存在、属于其他项目或不是当前版本')
                self._verified_path(row)
                refs.append((suggestion_id, document_id, page))
            db.execute('INSERT INTO suggestions VALUES (?, ?, ?, ?, 0)', (suggestion_id, self.entity, self.year, text))
            db.executemany('INSERT INTO suggestion_evidence VALUES (?, ?, ?)', refs)
        return suggestion_id

    def list_suggestions(self, current_only=False):
        docs = {doc['id']: doc for doc in self.list_documents()}
        with self._connect() as db:
            result = []
            references = {}
            for ref in db.execute('SELECT e.suggestion_id, e.document_id, e.page FROM suggestion_evidence e JOIN suggestions s ON s.id=e.suggestion_id WHERE s.entity=? AND s.year=? ORDER BY e.document_id, e.page', (self.entity, self.year)):
                references.setdefault(ref['suggestion_id'], []).append(ref)
            for row in db.execute('SELECT * FROM suggestions WHERE entity=? AND year=? ORDER BY rowid', (self.entity, self.year)):
                item = dict(row)
                refs = references.get(row['id'], [])
                item['evidence_ids'] = [f"{ref['document_id']}:{ref['page']}" for ref in refs]
                item['current'] = bool(refs) and all(ref['document_id'] in docs and docs[ref['document_id']]['current']
                                                   and docs[ref['document_id']]['text_layer'] for ref in refs)
                item['confirmed'] = bool(item['confirmed']) and item['current'] and all(docs[ref['document_id']]['reviewed'] for ref in refs)
                if not current_only or item['current']:
                    result.append(item)
            return result

    def confirm_suggestion(self, suggestion_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            suggestion = db.execute('SELECT id FROM suggestions WHERE id=? AND entity=? AND year=?',
                                    (suggestion_id, self.entity, self.year)).fetchone()
            refs = db.execute('SELECT d.*, d.id IN (SELECT document_id FROM text_layer_documents) AS text_layer '
                              'FROM suggestion_evidence e JOIN documents d ON d.id=e.document_id WHERE e.suggestion_id=?', (suggestion_id,)).fetchall()
            if not suggestion or not refs:
                raise ValueError('建议或关联证据不存在')
            for row in refs:
                latest = db.execute('SELECT MAX(version) FROM documents WHERE entity=? AND year=? AND name=? COLLATE CASEFOLD', (self.entity, self.year, row['name'])).fetchone()[0]
                if (row['entity'] != self.entity or row['year'] != self.year or row['version'] != latest
                        or not row['reviewed'] or not row['text_layer']):
                    raise ValueError('证据已过期或尚未复核')
                self._verified_path(row)
            db.execute('UPDATE suggestions SET confirmed=1 WHERE id=?', (suggestion_id,))

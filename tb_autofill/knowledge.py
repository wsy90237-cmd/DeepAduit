"""Local reference material and read-only ima OpenAPI retrieval."""

import hashlib
from contextlib import contextmanager
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

from .settings import validate_key

MAX_FILE = 12 * 1024 * 1024
MAX_TEXT = 300_000
FORMATS = (".txt", ".md", ".docx", ".pdf")


class KnowledgeError(Exception):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(text):
    parser = _Text()
    parser.feed(str(text or "")[:12000])
    return " ".join(parser.parts).strip()


def read_document(path):
    path = Path(path).resolve()
    if path.suffix.lower() not in FORMATS or not path.is_file():
        raise KnowledgeError("支持 TXT、Markdown、DOCX 和带文字层的 PDF。")
    before = path.stat()
    if before.st_size > MAX_FILE:
        raise KnowledgeError("单份资料最多 12 MB，请按章节拆分。")
    raw = path.read_bytes()
    if len(raw) > MAX_FILE:
        raise KnowledgeError("资料过大。")
    try:
        if path.suffix.lower() == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 2000 or sum(x.file_size for x in archive.infolist()) > 32 * 1024 * 1024:
                    raise KnowledgeError("DOCX 解压内容过大。")
                data = archive.read("word/document.xml")
            if len(data) > 8 * 1024 * 1024 or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                raise KnowledgeError("DOCX 包含不支持的 XML 内容。")
            tree = ET.fromstring(data)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n".join("".join(p.itertext()) for p in tree.findall(".//w:p", ns))
        elif path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise KnowledgeError("加密 PDF 请先在本机解锁后导入。")
            if len(reader.pages) > 200:
                raise KnowledgeError("单份 PDF 最多 200 页，请按章节拆分。")
            pieces, total = [], 0
            for page in reader.pages:
                piece = page.extract_text() or ""
                total += len(piece)
                if total > MAX_TEXT:
                    raise KnowledgeError("文字超过 30 万字符，请拆分资料。")
                pieces.append(piece)
            text = "\n".join(pieces)
        else:
            encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
            for encoding in encodings:
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeError:
                    continue
            else:
                raise KnowledgeError("无法识别文字编码，请另存为 UTF-8。")
    except KnowledgeError:
        raise
    except Exception:
        raise KnowledgeError("资料无法解析，请检查文件是否完整，或先导出为文本。") from None
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise KnowledgeError("读取期间资料发生变化，请保存文件后重新导入。")
    text = text.replace("\x00", "").strip()
    if not text:
        raise KnowledgeError("未提取到文字。扫描 PDF 需要先做 OCR，本版不会猜测扫描内容。")
    if len(text) > MAX_TEXT:
        raise KnowledgeError("文字超过 30 万字符，请拆分资料。")
    return text, hashlib.sha256(raw).hexdigest()


class LocalLibrary:
    def __init__(self, path=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZSZH-TB"
        self.path = Path(path) if path else base / "knowledge.sqlite3"

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, title TEXT, path TEXT, text TEXT, added TEXT DEFAULT CURRENT_TIMESTAMP)")
                yield db
        finally:
            db.close()

    def import_file(self, path):
        text, digest = read_document(path)
        with self._connect() as db:
            exists = db.execute("SELECT id FROM documents WHERE id=?", (digest,)).fetchone()
            if not exists:
                count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                if count >= 200:
                    raise KnowledgeError("本地库本版最多 200 份资料，请按主题精选引用材料。")
                db.execute("INSERT INTO documents(id,title,path,text) VALUES(?,?,?,?)",
                           (digest, Path(path).stem[:200], str(Path(path).resolve()), text))
        return digest, exists is None

    def list_documents(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT id,title,path,added FROM documents ORDER BY added DESC LIMIT 200")]

    def search(self, query, limit=8):
        query = str(query or "").strip()
        if not 2 <= len(query) <= 120:
            raise KnowledgeError("检索词长度为 2–120 个字符。")
        words = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]{2,}", query.lower())
        terms = set(words)
        for word in words:
            if re.fullmatch(r"[\u4e00-\u9fff]+", word) and len(word) > 2:
                terms.update(word[i:i + 2] for i in range(len(word) - 1))
        terms = sorted(terms, key=len, reverse=True)[:40]
        if not terms:
            raise KnowledgeError("请输入可检索的中文词或英文关键词。")
        found = []
        with self._connect() as db:
            for row in db.execute("SELECT id,title,path,text FROM documents"):
                text = row["text"]
                best = None
                for start in range(0, len(text), 1000):
                    chunk = text[max(0, start - 120):start + 1200]
                    score = (sum(len(term) ** 2 for term in terms if term in chunk.lower()) * 4
                             + sum(len(term) ** 2 for term in terms if term in row["title"].lower()))
                    if score and (best is None or score > best[0]):
                        best = (score, max(0, start - 120), chunk)
                if best:
                    found.append({"source": "local", "id": row["id"], "title": row["title"],
                                  "locator": f"本地资料 {row['id'][:12]} · 字符 {best[1] + 1}",
                                  "excerpt": best[2], "score": best[0]})
        return sorted(found, key=lambda item: item["score"], reverse=True)[:limit]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise KnowledgeError("ima 返回重定向，已停止请求；凭证不会转发。")


class ImaClient:
    METHODS = {"search_knowledge_base", "get_knowledge_base", "get_knowledge_list", "search_knowledge"}

    def __init__(self, client_id, api_key, *, transport=None):
        self.client_id, self.api_key = validate_key(client_id), validate_key(api_key)
        self.transport = transport

    def _post(self, method, payload):
        if method not in self.METHODS:
            raise KnowledgeError("本版 ima 接入仅支持只读检索。")
        try:
            if self.transport:
                response = self.transport(method, payload)
            else:
                request = urllib.request.Request("https://ima.qq.com/openapi/wiki/v1/" + method,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "ima-openapi-clientid": self.client_id,
                             "ima-openapi-apikey": self.api_key})
                with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as stream:
                    raw = stream.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise KnowledgeError("ima 返回过大，请缩小检索范围。")
                response = json.loads(raw)
            if not isinstance(response, dict) or type(response.get("code")) is not int:
                raise KnowledgeError("ima 返回结构不符合官方协议。")
            if response["code"] != 0:
                labels = {110030: "无权访问该知识库", 110021: "请求频率受限", 110001: "参数不合法", 110002: "凭证或服务配置不合法"}
                raise KnowledgeError(f"ima：{labels.get(response['code'], '接口拒绝请求')}（{response['code']}）。请在 ima 核对账号权限。")
            if not isinstance(response.get("data"), dict):
                raise KnowledgeError("ima 未返回有效数据。")
            return response["data"]
        except KnowledgeError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise KnowledgeError("ima 连接失败或返回无法解析；请检查凭证、网络和账号权限。") from None

    def search_libraries(self, query, cursor=""):
        query = str(query or "").strip()
        if not 1 <= len(query) <= 120:
            raise KnowledgeError("请输入 1–120 个字符的知识库名称。")
        value = self._post("search_knowledge_base", {"query": query, "cursor": cursor, "limit": 20})
        items = value.get("info_list")
        if not isinstance(items, list):
            raise KnowledgeError("ima 知识库列表格式不正确。")
        if len(items) > 20:
            raise KnowledgeError("ima 超过单页显示上限，请缩小库名检索范围。")
        return [{"id": item["id"], "name": str(item.get("name", ""))[:200]}
                for item in items[:20] if isinstance(item, dict) and isinstance(item.get("id"), str)], self._cursor(value)

    @staticmethod
    def _cursor(value):
        if value.get("is_end") is True:
            return ""
        cursor = value.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
            raise KnowledgeError("ima 分页信息不完整，未将结果视为全量。")
        return cursor

    def search(self, library_id, query, cursor=""):
        if not isinstance(library_id, str) or not 1 <= len(library_id) <= 512:
            raise KnowledgeError("请先选择有效的 ima 知识库。")
        query = str(query or "").strip()
        if not 1 <= len(query) <= 120:
            raise KnowledgeError("请输入 1–120 个字符的检索词。")
        value = self._post("search_knowledge", {"knowledge_base_id": library_id, "query": query, "cursor": cursor})
        items = value.get("info_list")
        if not isinstance(items, list):
            raise KnowledgeError("ima 检索结果格式不正确。")
        if len(items) > 50:
            raise KnowledgeError("ima 单页结果过多，请缩小检索词，未将截取结果当作全量。")
        refs = []
        for item in items[:50]:
            if isinstance(item, dict) and isinstance(item.get("media_id"), str):
                refs.append({"source": "ima", "id": item["media_id"], "title": str(item.get("title", ""))[:200],
                             "locator": f"ima 知识库 {library_id} · 资料 {item['media_id']}",
                             "excerpt": plain_text(item.get("highlight_content", ""))[:1800]})
        return refs, self._cursor(value)

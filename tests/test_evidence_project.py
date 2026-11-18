from pathlib import Path

import tempfile
import unittest

from tb_autofill.evidence_project import ProjectStore


def test_versions_review_and_project_isolation(tmp_path):
    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'%PDF-1.4 original')
    store = ProjectStore(tmp_path / 'storage', '公司甲', 2025)
    first = store.import_pdf(pdf, lambda path: ['第一页', '第二页'])
    assert first['version'] == 1 and first['pages'][0]['text'] == '第一页'
    assert store.import_pdf(pdf, lambda path: ['第一页'])['id'] == first['id']
    evidence = first['pages'][0]['evidence_id']
    suggestion = store.add_suggestion('{"content":"核对"}', [evidence])
    with unittest.TestCase().assertRaises(ValueError):
        store.confirm_suggestion(suggestion)
    store.mark_reviewed(first['id'])
    store.confirm_suggestion(suggestion)
    assert store.list_suggestions()[0]['confirmed']
    other = ProjectStore(tmp_path / 'storage', '公司乙', 2025)
    with unittest.TestCase().assertRaises(ValueError):
        other.add_suggestion('其他主体', [evidence])
    assert ProjectStore(tmp_path / 'storage', '公司甲', 2024).list_documents() == []
    pdf.write_bytes(b'%PDF-1.4 changed')
    second = store.import_pdf(pdf, lambda path: ['新版'])
    assert second['version'] == 2 and second['id'] != first['id']
    assert not store.list_suggestions()[0]['confirmed']
    assert not store.list_suggestions(current_only=True)
    with unittest.TestCase().assertRaises(ValueError):
        store.mark_reviewed(first['id'])
    assert Path(first['pdf_path']).read_bytes() == b'%PDF-1.4 original'
    assert store.verify_document(first['id']) == Path(first['pdf_path'])
    assert 'pages' not in store.list_documents()[0]
    assert store.get_document(first['id'])['pages'][0]['text'] == '第一页'
    pdf.write_bytes(b'%PDF-1.4 original')
    assert store.import_pdf(pdf, lambda path: ['恢复'])['version'] == 3


def test_extractor_sees_copy_and_failures_leave_no_records(tmp_path):
    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'%PDF-1.4 original')
    store = ProjectStore(tmp_path / 'storage', '公司甲', 2025)

    def extractor(snapshot):
        assert snapshot != pdf
        assert snapshot.read_bytes() == b'%PDF-1.4 original'
        snapshot.write_bytes(b'%PDF-1.4 modified')
        return ['文本']

    with unittest.TestCase().assertRaises(ValueError):
        store.import_pdf(pdf, extractor)
    assert store.list_documents() == []
    assert not list(store.files.iterdir())
    assert pdf.read_bytes() == b'%PDF-1.4 original'


class EvidenceProjectTests(unittest.TestCase):
    def test_versions_and_isolation(self):
        with tempfile.TemporaryDirectory() as path:
            test_versions_review_and_project_isolation(Path(path))

    def test_copy_integrity(self):
        with tempfile.TemporaryDirectory() as path:
            test_extractor_sees_copy_and_failures_leave_no_records(Path(path))

    def test_open_existing_does_not_create_project(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path) / 'projects'
            self.assertIsNone(ProjectStore.open_existing(root, '公司', 2025))
            self.assertFalse(root.exists())
            created = ProjectStore(root, '公司', 2025)
            reopened = ProjectStore.open_existing(root, '公司', 2025)
            self.assertEqual(reopened.root, created.root)
            self.assertIsNone(ProjectStore.open_existing(root, '其他公司', 2025))

    def test_tamper_and_casefold(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = root / 'Evidence.pdf'
            source.write_bytes(b'%PDF-1.4 first')
            store = ProjectStore(root / 'projects', 'A [id1]', '2025')
            first = store.import_pdf(source, lambda path: ['text'])
            source = root / 'evidence.PDF'
            source.write_bytes(b'%PDF-1.4 second')
            second = store.import_pdf(source, lambda path: ['text'])
            self.assertEqual(second['version'], 2)
            self.assertNotEqual(ProjectStore(root / 'projects', 'A [id2]', 2025).root, store.root)
            self.assertEqual(store.verify_document(second['id']), Path(second['pdf_path']))
            store.mark_reviewed(second['id'])
            suggestion = store.add_suggestion('text', [second['pages'][0]['evidence_id']])
            Path(second['pdf_path']).write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                store.verify_document(second['id'])
            with self.assertRaises(ValueError):
                store.confirm_suggestion(suggestion)
            with self.assertRaises(ValueError):
                ProjectStore(root, 'A', '../2025')
            reserved = ProjectStore(root / 'projects', 'CON', 2025)
            self.assertTrue(reserved.root.parent.name.startswith('项目_CON-'))
            traversal = ProjectStore(root / 'projects', '../公司:测试 ', 2025)
            self.assertEqual(traversal.root.parent.parent, (root / 'projects').resolve())

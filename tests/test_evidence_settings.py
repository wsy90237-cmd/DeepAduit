import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tb_autofill.settings import SettingsError, SettingsStore


class EvidenceRootSettingsTests(unittest.TestCase):
    def test_persists_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            store = SettingsStore(path)
            store.save_signers('编制人', '复核人')
            root = str(Path(folder) / 'projects')
            store.save_evidence_root(root)
            reopened = SettingsStore(path)
            self.assertEqual(reopened.data['evidence_root'], root)
            self.assertEqual(reopened.data['prepared_by'], '编制人')
            self.assertEqual(reopened.data['reviewed_by'], '复核人')
            with self.assertRaises(SettingsError):
                store.save_evidence_root('relative')
            with patch.object(store, '_write', side_effect=SettingsError('failed')):
                with self.assertRaises(SettingsError):
                    store.save_evidence_root(str(Path(folder) / 'other'))
            self.assertEqual(store.data['evidence_root'], root)

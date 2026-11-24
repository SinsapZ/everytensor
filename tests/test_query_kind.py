"""Exercise the desktop's query/type transitions through its real Qt controller."""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from everytensor import indexer, store
from everytensor.client import Controller


class QueryKindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.client = Controller(self.root/"data", start_backend=False)
        for pool in self.client._pools.values():
            self.addCleanup(pool.shutdown, wait=True)
        self.addCleanup(self.client.close)

    def test_generic_query_after_media_finds_document(self):
        folder = self.root/"corpus"
        folder.mkdir()
        (folder/"archive.txt").write_text("Accession number QV-7319.", "utf-8")
        store.set_setting(self.client.db, "scope", "folders")
        store.set_setting(self.client.db, "roots", [str(folder)])
        indexer.scan(self.client.db, str(folder))
        indexer.index_pending(self.client.db)
        for query, expected in (("红色圆形的图片", "image"), ("铃声的录音", "audio"), ("QV-7319", "")):
            self.client.setQuery(query)
            self.assertEqual(self.client.kind, expected)
        self.client._issue_semantic()
        self.assertEqual(self.client.commands.messages[-1]["kind"], "")
        # Wait for both real SQLite readers, including their completion callbacks.
        for pool in self.client._pools.values():
            pool.submit(lambda: None).result(timeout=5)
        self.client.poll()
        self.assertEqual([row["name"] for row in self.client.rows], ["archive.txt"])

    def test_manual_tab_returns_after_query_with_filename_type(self):
        self.client.setKind("document")
        self.client.setQuery("poster.png")
        self.assertEqual(self.client.kind, "image")
        self.client.setQuery("借伞")
        self.assertEqual(self.client.kind, "document")

    def test_bulk_edits_and_manual_filters_do_not_wait_for_typing(self):
        self.client.setQuery("Which file mentions the winter trip?")
        self.assertEqual(self.client._debounce.interval(), 60)
        self.client.setQuery("Which file mentions a winter trip?")
        self.assertEqual(self.client._debounce.interval(), 220)
        self.client.setQuery("Which file mentions a long summer holiday trip?")
        self.assertEqual(self.client._debounce.interval(), 60)
        self.client.setKind("document")
        self.assertEqual(self.client._debounce.interval(), 0)

    def test_confirming_inferred_tab_persists_without_restarting_search(self):
        self.client.setQuery("videos of walking")
        previous_generation = self.client.generation
        self.client.setKind("video")
        self.assertEqual(self.client.generation, previous_generation)
        self.client.setQuery("海边")
        self.assertEqual(self.client.kind, "video")

    def test_first_escape_clears_query_second_clears_tab(self):
        self.client.setQuery("录音")
        self.client.setQuery("")
        self.assertEqual((self.client.query, self.client.kind), ("", "audio"))
        self.client.setQuery("借伞")
        self.assertEqual(self.client.kind, "")
        self.client.setKind("code")
        self.client.setQuery("")
        self.assertEqual(self.client.kind, "code")
        self.client.setKind("")
        self.client.setQuery("借伞")
        self.assertEqual(self.client.kind, "")

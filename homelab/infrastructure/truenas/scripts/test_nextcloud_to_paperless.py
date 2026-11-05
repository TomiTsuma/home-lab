"""Tests for nextcloud_to_paperless.py, run against temp dirs (no Nextcloud/Paperless needed).

Usage: python3 test_nextcloud_to_paperless.py  (from the scripts directory)
"""
import os
import tempfile
import time
import unittest
from unittest import mock

import nextcloud_to_paperless as sync


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.files = os.path.join(root, "files")
        self.consume = os.path.join(root, "consume")
        os.makedirs(os.path.join(self.files, "Geothermal", "papers"))
        os.makedirs(self.consume)
        patches = {"NC_FILES": self.files, "CONSUME": self.consume,
                   "MIRROR_FOLDERS": ["Geothermal"], "LOG_FILE": os.path.join(root, "log"),
                   "INBOX_MIN_AGE": 0, "MAX_PER_RUN": 100, "DRY": False}
        for k, v in patches.items():
            p = mock.patch.object(sync, k, v)
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(sync.os, "chown"), mock.patch.object(sync, "nextcloud_rescan")):
            self.rescan = p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, data):
        path = os.path.join(self.files, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def consumed(self):
        out = []
        for root, _, files in os.walk(self.consume):
            out += [os.path.relpath(os.path.join(root, f), self.consume) for f in files]
        return sorted(out)

    def test_mirror_copies_once_keeps_source_and_path(self):
        src = self.write("Geothermal/papers/a.pdf", b"one")
        state = {}
        self.assertEqual(sync.mirror(state, 100), 1)
        self.assertEqual(self.consumed(), [os.path.join("Geothermal", "papers", "a.pdf")])
        self.assertTrue(os.path.exists(src))  # Nextcloud copy untouched
        os.remove(os.path.join(self.consume, "Geothermal", "papers", "a.pdf"))  # "consumed"
        self.assertEqual(sync.mirror(state, 100), 0)  # not re-sent

    def test_edited_file_is_resent_touched_file_is_not(self):
        src = self.write("Geothermal/a.pdf", b"one")
        state = {}
        sync.mirror(state, 100)
        os.utime(src, (time.time() + 10, time.time() + 10))  # touch, same content
        self.assertEqual(sync.mirror(state, 100), 0)
        self.write("Geothermal/a.pdf", b"two")
        self.assertEqual(sync.mirror(state, 100), 1)

    def test_unsupported_and_hidden_files_skipped(self):
        self.write("Geothermal/notes.docx", b"x")
        self.write("Geothermal/.hidden.pdf", b"x")
        self.assertEqual(sync.mirror({}, 100), 0)

    def test_budget_limits_files_per_run(self):
        for i in range(5):
            self.write(f"Geothermal/{i}.pdf", str(i).encode())
        state = {}
        self.assertEqual(sync.mirror(state, 2), 2)
        self.assertEqual(sync.mirror(state, 10), 3)

    def test_inbox_created_then_files_moved(self):
        self.assertEqual(sync.inbox(), 0)
        self.assertTrue(os.path.isdir(os.path.join(self.files, sync.INBOX)))
        src = self.write(f"{sync.INBOX}/scan.pdf", b"scan")
        self.write(f"{sync.INBOX}/sheet.xlsx", b"x")
        self.assertEqual(sync.inbox(), 1)
        self.assertFalse(os.path.exists(src))  # moved out of Nextcloud
        self.assertTrue(os.path.exists(os.path.join(self.files, sync.INBOX, "sheet.xlsx")))
        [moved] = self.consumed()
        self.assertTrue(moved.startswith("Inbox" + os.sep) and moved.endswith("_scan.pdf"))

    def test_no_part_files_left_behind(self):
        self.write("Geothermal/a.pdf", b"one")
        sync.mirror({}, 100)
        self.assertFalse([f for f in self.consumed() if f.endswith(".part")])


if __name__ == "__main__":
    unittest.main()

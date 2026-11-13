"""Offline regression tests for stale progress and OS process locks."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import camping_common as core


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "camping_state.json"
        self.lock_path = self.root / "camping.lock"
        self.previous = {"phase": "REVIEW_REQUIRED", "dates": {"2026-11-21": "AVAILABLE"},
                         "active": {"site": "A107", "checkin": "2026-11-21", "nights": 1}}
        core.save(self.state, self.previous)

    def child(self, source):
        return subprocess.run([sys.executable, "-X", "utf8", "-c", source, str(self.lock_path)],
                              cwd=Path(core.__file__).parent, capture_output=True, timeout=15)

    def test_recover_only_progress_preserving_original_and_settings(self):
        settings = self.root / "camping_settings.json"
        env = self.root / ".env"
        settings.write_bytes(b'{"site":"A*","nights":1}')
        env.write_bytes(b"TEST_PRIVATE_VALUE=unchanged")
        original = self.state.read_bytes()
        with core.acquire_lock(self.lock_path) as lock:
            archive = core.recover_previous_state(self.state, lock)
            self.assertEqual(archive.read_bytes(), original)
            recovered = json.loads(self.state.read_text(encoding="utf-8"))
            self.assertNotIn("active", recovered)
            self.assertEqual(recovered["phase"], "RECOVERED")
            self.assertEqual(recovered["dates"], self.previous["dates"])
            self.assertIsNone(core.recover_previous_state(self.state, lock))
        self.assertEqual(settings.read_bytes(), b'{"site":"A*","nights":1}')
        self.assertEqual(env.read_bytes(), b"TEST_PRIVATE_VALUE=unchanged")

    def test_running_process_lock_blocks_without_changing_state(self):
        original = self.state.read_bytes()
        with core.acquire_lock(self.lock_path):
            result = self.child("import sys; from pathlib import Path; import camping_common as c; "
                                "c.acquire_lock(Path(sys.argv[1]))")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse((self.root / "state_history").exists())

    def test_crash_releases_lock_and_stale_record_recovers(self):
        result = self.child("import os, sys; from pathlib import Path; import camping_common as c; "
                            "lock=c.acquire_lock(Path(sys.argv[1])); os._exit(7)")
        self.assertEqual(result.returncode, 7)
        with core.acquire_lock(self.lock_path) as lock:
            self.assertIsNotNone(core.recover_previous_state(self.state, lock))
        self.assertNotIn("active", json.loads(self.state.read_text(encoding="utf-8")))

    def test_closed_lock_cannot_reset_state(self):
        lock = core.acquire_lock(self.lock_path)
        lock.close()
        with self.assertRaises(ValueError):
            core.recover_previous_state(self.state, lock)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8")), self.previous)


if __name__ == "__main__":
    unittest.main()

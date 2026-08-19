import os
import tempfile
import unittest

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.storage import AtomicJsonStore


class StorageTests(unittest.TestCase):
    def test_latest_and_node_state_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            main = AtomicJsonStore(directory, "main")
            slave = AtomicJsonStore(directory, "slave")
            main.write_latest({"sequence": 2})
            main.write_state({"exported_sequence": 2})
            slave.write_state({"applied_sequence": 2})
            self.assertEqual(slave.read_latest()["sequence"], 2)
            self.assertEqual(main.read_state(), {"exported_sequence": 2})
            self.assertEqual(slave.read_state(), {"applied_sequence": 2})

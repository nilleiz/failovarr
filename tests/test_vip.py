import os
import unittest
from unittest.mock import Mock, patch

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.vip import VipManager


class VipSafetyTests(unittest.TestCase):
    def test_primary_address_is_not_treated_as_vip(self):
        output = '[{"addr_info":[{"family":"inet","local":"192.168.1.10","scope":"global"}]}]'
        with patch("failovarr.vip.subprocess.run", return_value=Mock(stdout=output)):
            manager = VipManager("192.168.1.10", "eth0", 24)
            with self.assertRaisesRegex(RuntimeError, "primary address"):
                manager.acquire()

    def test_second_address_is_recognized_as_secondary(self):
        output = (
            '[{"addr_info":['
            '{"family":"inet","local":"192.168.1.10","scope":"global"},'
            '{"family":"inet","local":"192.168.1.20","scope":"global"}'
            ']}]'
        )
        with patch("failovarr.vip.subprocess.run", return_value=Mock(stdout=output)):
            manager = VipManager("192.168.1.20", "eth0", 24)
            self.assertTrue(manager._is_secondary())

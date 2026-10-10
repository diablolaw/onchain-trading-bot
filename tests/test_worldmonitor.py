import unittest
from unittest.mock import Mock
from desk.worldmonitor import WorldMonitor

class WorldMonitorTests(unittest.TestCase):
    def test_only_local_origins(self):
        for url in ('https://example.com','http://localhost.evil.com','http://user@localhost','http://127.0.0.1/path'):
            with self.assertRaises(ValueError):WorldMonitor(url)
    def test_degraded_200_payload_is_unavailable(self):
        wm=WorldMonitor();wm.get=Mock(return_value={'unavailable':True})
        self.assertFalse(wm.health()['ready'])
        wm.get=Mock(return_value={'provider':'degraded','quotes':[]})
        self.assertFalse(wm.health()['ready'])

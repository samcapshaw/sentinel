import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from public_feeds import fetch

class FeedCacheTests(unittest.TestCase):
    def test_outage_retains_original_timestamp_and_marks_stale(self):
        with tempfile.TemporaryDirectory() as directory,patch('public_feeds.Path.home',return_value=Path(directory)):
            response=MagicMock();response.__enter__.return_value.read.return_value=b'{"value":1}'
            with patch('public_feeds.urllib.request.urlopen',return_value=response),patch('public_feeds.time.time',return_value=1000):
                self.assertEqual(fetch('https://example.com/feed'),({'value':1},1000,False))
            with patch('public_feeds.urllib.request.urlopen',side_effect=OSError('offline')),patch('public_feeds.time.time',return_value=2000):
                self.assertEqual(fetch('https://example.com/feed'),({'value':1},1000,True))
    def test_recent_cache_avoids_network_and_invalid_json_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory,patch('public_feeds.Path.home',return_value=Path(directory)):
            response=MagicMock();response.__enter__.return_value.read.return_value=b'{"value":1}'
            with patch('public_feeds.urllib.request.urlopen',return_value=response) as network:
                fetch('https://example.com/feed');fetch('https://example.com/feed');self.assertEqual(network.call_count,1)
            response.__enter__.return_value.read.return_value=b'<html>Error</html>'
            with patch('public_feeds.urllib.request.urlopen',return_value=response):
                with self.assertRaises(ValueError):fetch('https://example.com/bad')

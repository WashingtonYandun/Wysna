"""Transient component downloads preserve the pinned checksum boundary."""
from email.utils import formatdate
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.package_components import download


class ComponentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.data = b'component contents'
        self.component = {
            'url': 'https://example.invalid/component.tar',
            'sha256': hashlib.sha256(self.data).hexdigest(),
        }

    def error(self, code, headers=None):
        return HTTPError(self.component['url'], code, 'HTTP failure', headers or {}, None)

    @patch('tools.package_components.time.sleep')
    @patch('tools.package_components.urllib.request.urlopen')
    def test_rate_limit_retries_then_verifies_and_caches(self, open_url, sleep):
        open_url.side_effect = [self.error(429, {'Retry-After': '3'}), io.BytesIO(self.data)]
        target = download(self.component, self.cache)
        self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(3)
        self.assertEqual(download(self.component, self.cache), target)
        self.assertEqual(open_url.call_count, 2)

    @patch('tools.package_components.time.time', return_value=1000)
    @patch('tools.package_components.time.sleep')
    @patch('tools.package_components.urllib.request.urlopen')
    def test_rate_limit_http_date_is_respected(self, open_url, sleep, _clock):
        retry_at = formatdate(1030, usegmt=True)
        open_url.side_effect = [self.error(429, {'Retry-After': retry_at}), io.BytesIO(self.data)]
        download(self.component, self.cache)
        sleep.assert_called_once_with(30)

    @patch('tools.package_components.time.sleep')
    @patch('tools.package_components.urllib.request.urlopen')
    def test_rate_limit_exceeding_wait_budget_fails_without_retry(self, open_url, sleep):
        open_url.side_effect = self.error(429, {'Retry-After': '61'})
        with self.assertRaises(HTTPError):
            download(self.component, self.cache)
        self.assertEqual(open_url.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(list(self.cache.iterdir()), [])

    @patch('tools.package_components.time.sleep')
    @patch('tools.package_components.urllib.request.urlopen')
    def test_permanent_http_error_is_not_retried(self, open_url, sleep):
        open_url.side_effect = self.error(404)
        with self.assertRaises(HTTPError):
            download(self.component, self.cache)
        self.assertEqual(open_url.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(list(self.cache.iterdir()), [])

    @patch('tools.package_components.time.sleep')
    @patch('tools.package_components.urllib.request.urlopen')
    def test_persistent_rate_limit_is_bounded(self, open_url, sleep):
        open_url.side_effect = [self.error(429) for _ in range(4)]
        with self.assertRaises(HTTPError):
            download(self.component, self.cache)
        self.assertEqual(open_url.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])
        self.assertEqual(list(self.cache.iterdir()), [])

    @patch('tools.package_components.urllib.request.urlopen')
    def test_checksum_mismatch_never_enters_cache(self, open_url):
        open_url.return_value = io.BytesIO(b'incorrect')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            download(self.component, self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

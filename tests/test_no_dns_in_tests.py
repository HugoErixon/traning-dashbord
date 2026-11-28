"""Testerna får inte slå upp påhittade värdnamn i DNS.

Tidigare pekade DATABASE_URL på värden 'unused-in-tests'. Varje test som
försökte nå databasen gjorde då ett DNS-uppslag, och en hel testkörning på G3
blev över 1 000 uppslag i minuten. Pi-hole stängde då av DNS för G3 en stund
(rate limit), så git, Garmin-synk och allt annat på maskinen tappade nätet
(2026-11-28). En IP-adress avvisas direkt utan uppslag.
"""
import ipaddress
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

TESTS = Path(__file__).resolve().parent


class NoDnsInTestsTests(unittest.TestCase):
    def test_database_urls_in_tests_use_an_ip_address(self):
        pattern = re.compile(r"DATABASE_URL'\]\s*=\s*'([^']+)'")
        found = 0
        for path in sorted(TESTS.glob('test_*.py')):
            for url in pattern.findall(path.read_text(encoding='utf-8')):
                found += 1
                with self.subTest(file=path.name):
                    host = urlparse(url).hostname
                    ipaddress.ip_address(host)  # ValueError om det är ett namn
        self.assertGreater(found, 0)


if __name__ == '__main__':
    unittest.main()

import unittest

from l610 import parse_csq_rssi


class L610Tests(unittest.TestCase):
    def test_parse_csq_rssi(self):
        self.assertEqual(parse_csq_rssi("+CSQ: 29,99\r\nOK"), -55)
        self.assertEqual(parse_csq_rssi("+CSQ: 99,99\r\nOK"), 0)
        self.assertEqual(parse_csq_rssi("ERROR"), 0)


if __name__ == "__main__":
    unittest.main()

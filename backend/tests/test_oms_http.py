import unittest

from app.services.oms_http import classify_http_status, request_exception_outcome
import requests


class TestOmsHttp(unittest.TestCase):
    def test_401_is_error(self):
        out = classify_http_status(401)
        self.assertEqual(out["status"], "error")

    def test_400_is_error(self):
        out = classify_http_status(400, "bad qty")
        self.assertEqual(out["status"], "error")

    def test_500_is_ambiguous(self):
        out = classify_http_status(503, "gateway")
        self.assertEqual(out["status"], "ambiguous")

    def test_timeout_is_ambiguous(self):
        out = request_exception_outcome(requests.Timeout("timed out"))
        self.assertEqual(out["status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()

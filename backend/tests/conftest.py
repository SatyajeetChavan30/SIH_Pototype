"""Tests run with sign-in off unless a test turns it on (test_auth.py), so API tests exercise features, not login."""
import os

os.environ.setdefault("NWIS_AUTH", "off")

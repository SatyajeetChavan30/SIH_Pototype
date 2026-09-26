"""Tests run with sign-in off unless a test turns it on (test_auth.py), so API tests exercise features, not login.
Dashboard settings go to a throw-away file so a developer's own stratasense_settings.json never changes test results."""
import os
import tempfile

os.environ.setdefault("STRATASENSE_AUTH", "off")
os.environ.setdefault("STRATASENSE_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="stratasense-test-"), "settings.json"))

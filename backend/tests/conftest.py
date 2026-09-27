"""Tests run with sign-in off unless a test turns it on (test_auth.py), so API tests exercise features, not login.
Dashboard settings go to a throw-away file so a developer's own stratasense_settings.json never changes test results."""
import os
import tempfile

os.environ.setdefault("STRATASENSE_AUTH", "off")
if "STRATASENSE_SETTINGS" not in os.environ:
    os.environ["STRATASENSE_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="stratasense-test-"), "settings.json")
    # StrataSense opens on the real North Sea data when it is built; the system tests measure the synthetic Assam demo
    with open(os.environ["STRATASENSE_SETTINGS"], "w", encoding="utf-8") as f:
        f.write('{"dataset": "assam"}')

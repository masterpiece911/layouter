"""Firefox declaration validation and stable identity regression coverage."""
from dataclasses import replace
from pathlib import Path
import unittest

from layouter.config import resolve
from layouter.errors import ConfigError
from layouter.model import FirefoxTab
from layouter.schema import normalize_document


def workflow(**fields):
    return resolve(normalize_document({"session": "dev", "workspace": [{"number": 3,
        "firefox": [{"name": "work", **fields}]}]}), Path.cwd())


class FirefoxConfigTests(unittest.TestCase):
    def test_defaults_literal_tab_identity_and_parent_scope(self):
        w = workflow(tab=[{"url": "https://example.com/a?b=c", "pinned": True},
                          {"name": "jira", "url": "https://jira", "active": True}])
        n = w.leaves[0]
        self.assertEqual((n.kind, n.executable, n.args), ("firefox", "firefox", ()))
        self.assertEqual(n.firefox_tabs[0], FirefoxTab("https://example.com/a?b=c", "https://example.com/a?b=c", True))
        self.assertEqual(n.firefox_tabs[1].id, "jira")
        data = {"workspace": [{"number": 3, "firefox": [
            {"name": name, "tab": [{"url": "https://same"}]} for name in ("one", "two")]}]}
        self.assertEqual(len(resolve(normalize_document(data), Path.cwd()).leaves), 2)

    def test_launch_identity_and_legacy_identity(self):
        w = workflow(args=["-P", "Work"])
        n = w.leaves[0]
        self.assertEqual(w.element_key(n), workflow(args=["-P", "Work"]).element_key(n))
        for changed in (replace(n, args=("-P", "Personal")), replace(n, executable="zen-browser"),
                        replace(n, args=("-P Work",))):
            self.assertNotEqual(w.element_key(n), w.element_key(changed))
            self.assertNotEqual(w.mark_for(n), w.mark_for(changed))
        for kind in ("app", "kitty", "container"):
            old = replace(n, kind=kind)
            self.assertEqual(w.element_key(old), w.element_id(old.id))
            self.assertEqual(w.mark_for(old), w.mark(old.id))

    def test_invalid_tabs_and_fields(self):
        invalid = [dict(tab=[{"url": "x"}, {"url": "x"}]),
                   dict(tab=[{"name": "n", "url": "x"}, {"name": "n", "url": "y"}]),
                   dict(tab=[{"url": "x", "active": True}, {"url": "y", "active": True}]),
                   dict(tab=[{"url": ""}]), dict(tab=[{"name": "", "url": "x"}]),
                   dict(tab=[{"url": "x", "pinned": 1}]), dict(tab=[{}]),
                   dict(args="-P Work"), dict(args=[1]), dict(width=500),
                   dict(floating=True, size=20)]
        invalid += [{field: True} for field in ("command", "match", "adopt", "private", "profile", "initial_urls", "initial_tabs")]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ConfigError):
                workflow(**fields)

    def test_disabled_and_floating(self):
        self.assertEqual(workflow(enabled=False).leaves, ())
        self.assertEqual(workflow(tab=[{"enabled": False}]).leaves[0].firefox_tabs, ())
        w = resolve(normalize_document({"workspace": [{"number": 3, "floating": {
            "firefox": [{"name": "scratch", "width": 500}]}}]}), Path.cwd())
        self.assertTrue(w.leaves[0].floating)
        self.assertEqual(w.leaves[0].width, 500)
        workflow(tab=[{"url": "x", "active": True}, {"url": "y", "active": True, "enabled": False}])

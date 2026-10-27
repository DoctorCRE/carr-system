#!/usr/bin/env python3
"""Synthetic coverage of the contact-view interface shared by Outlook callers."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("calendar_touch_matcher", REPO / "tools/calendar-touch-matcher.py")
assert spec is not None and spec.loader is not None
matcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matcher)


class RecordViews(unittest.TestCase):
    def test_reads_columns_and_rows_with_the_callers_credential_pointer(self):
        cursor = mock.MagicMock()
        cursor.description = [("Client ID",), ("Name",), ("Email",)]
        cursor.fetchall.return_value = [("C-1", "Client One", "one@example.test"),
                                       ("C-2", "Client Two", None)]
        connection = mock.MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        credential = Path("/synthetic/private/database-config")
        with mock.patch("exporters.common.connect", return_value=connection) as connect:
            columns, rows = matcher.read_view("v_export_clients", credential_file=credential)
        connect.assert_called_once_with(credential_file=credential)
        cursor.execute.assert_called_once_with("select * from v_export_clients")
        self.assertEqual(columns, ["Client ID", "Name", "Email"])
        self.assertEqual(rows, [{"Client ID": "C-1", "Name": "Client One", "Email": "one@example.test"},
                                {"Client ID": "C-2", "Name": "Client Two", "Email": None}])

    def test_empty_view_retains_its_columns(self):
        cursor = mock.MagicMock()
        cursor.description = [("ID",), ("Name",), ("Email",)]
        cursor.fetchall.return_value = []
        connection = mock.MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with mock.patch("exporters.common.connect", return_value=connection):
            self.assertEqual(matcher.read_view("v_export_vendors"), (["ID", "Name", "Email"], []))

    def test_unreachable_view_propagates_to_the_caller(self):
        with mock.patch("exporters.common.connect", side_effect=RuntimeError("synthetic unavailable view")), \
                self.assertRaisesRegex(RuntimeError, "synthetic unavailable view"):
            matcher.read_view("v_export_leads")

    def test_views_and_columns_match_the_exporters(self):
        from exporters import targets
        columns = {"v_export_clients": targets.ROSTER_COLS, "v_export_leads": targets.REGISTRY_COLS,
                   "v_export_vendors": targets.VENDORS_COLS}
        self.assertEqual({view for view, *_ in matcher.RECORD_VIEWS}, set(columns))
        for view, id_col, name_col, org_col in matcher.RECORD_VIEWS:
            with self.subTest(view=view):
                self.assertLessEqual({id_col, name_col, org_col, "Email"}, set(columns[view]))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Read the record contact views shared by Outlook intake and send."""

import os
import sys

RECORD_VIEWS = (
    ("v_export_clients", "Client ID", "Name", "Practice / Entity"),
    ("v_export_leads", "Lead ID", "Contact Name", "Practice"),
    ("v_export_vendors", "ID", "Name", "Company"),
)


def read_view(view, *, credential_file=None):
    """Column names and all rows of one export view, through the exporters' login.

    ``view`` is always one of the RECORD_VIEWS constants, never caller input.
    exporters.common.connect resolves its own credential and exits when there
    is none; the caller treats that exit as an unreachable view.
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from exporters.common import connect
    with connect(credential_file=credential_file) as conn, conn.cursor() as cur:
        cur.execute(f"select * from {view}")
        cols = [d[0] for d in cur.description]
        return cols, [dict(zip(cols, row)) for row in cur.fetchall()]

"""Select cache paths proven exclusive to one decoded Outlook account.

The caller must establish the account key independently. This selector does not
copy files or infer account ownership from email addresses.
"""
from collections import defaultdict
import sqlite3

from lib.outlook_attachments import CACHE_PATH


def select_paths(db: sqlite3.Connection, account_id: int) -> set[str]:
    """Return paths whose every reference has one message and only this account."""
    ownership: dict[str, list[tuple[int, int, int | None]]] = defaultdict(list)
    for path, key, message, account in db.execute('''
        SELECT a.path, a.message_key, k.message_id, f.account_id
        FROM attachments a
        LEFT JOIN message_keys k ON k.local_key = a.message_key
        LEFT JOIN message_folders mf ON mf.message_id = k.message_id
        LEFT JOIN folders f ON f.id = mf.folder_id
    '''):
        match = CACHE_PATH.fullmatch(path or '')
        if match and match.group(3) not in ('.', '..'):
            ownership[path].append((key, message, account))
    return {
        path for path, refs in ownership.items()
        if all(message is not None and account == account_id for _, message, account in refs)
        and all(len({message for key2, message, _ in refs if key2 == key}) == 1
                for key, _, _ in refs)
    }

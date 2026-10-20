"""Cooperative traversal bounds for recoverable machine cleanup."""
import os


class BudgetExhausted(TimeoutError):
    pass


def check(deadline):
    if deadline is not None and deadline():
        raise BudgetExhausted('cleanup time budget exhausted')


def bounded(values, deadline):
    stream = iter(values)
    try:
        while True:
            check(deadline)
            try:
                value = next(stream)
            except StopIteration:
                check(deadline)
                return
            check(deadline)
            yield value
    finally:
        close = getattr(stream, 'close', None)
        if close:
            close()


def entries(path, deadline):
    check(deadline)
    with os.scandir(path) as stream:
        yield from bounded(stream, deadline)


def walk(path, deadline):
    pending = [os.fspath(path)]
    while pending:
        parent = pending.pop()
        dirs, files = [], []
        for entry in entries(parent, deadline):
            check(deadline)
            (dirs if entry.is_dir() else files).append(entry.name)
        yield parent, dirs, files
        for name in reversed(dirs):
            check(deadline)
            child = os.path.join(parent, name)
            if not os.path.islink(child):
                pending.append(child)

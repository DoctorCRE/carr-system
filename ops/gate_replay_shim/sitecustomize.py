"""sitecustomize for ops/gate-replay.py — pins the clock, refuses the network,
and records which repository modules a gate process executed.

Python imports a module named `sitecustomize` automatically at start-up when
one is on sys.path, and ops/gate-replay.py puts this directory first on
PYTHONPATH for every gate it runs. The gate itself, hooks/hook-meter-run.py
around it, and any Python child either of them starts all load it. Nothing
here runs unless CARR_GATE_REPLAY_EPOCH is set, so this file is inert on any
ordinary interpreter that happens to see it.

WHY EACH PART EXISTS. The replay snapshot has to come out byte-identical on a
fresh Linux runner and on a developer Mac. Three inputs would otherwise differ:

  * THE CLOCK. One gate acts only at weekends, several compare against "the
    last 14 days", and any of them can print a date. time.time, time.time_ns,
    the no-argument forms of localtime/gmtime/ctime/strftime, and
    datetime.now/utcnow/today plus date.today use a logical clock. Each clock
    read advances one millisecond and explicit sleeps advance their requested
    duration. Host scheduling cannot spend a fixture's deadline budget. The
    parent's invocation timeout and stdlib blocking waits retain real time,
    so a hung gate still fails rather than spinning forever.
  * THE NETWORK. socket connect and name resolution raise OSError, so a gate
    that would call a vendor or the record layer takes its offline path in the
    same way everywhere and in milliseconds.
  * COVERAGE EVIDENCE. An audit hook records every Python file under the
    sandbox checkout that the process compiles, which is every module it
    executes: the import system compiles through the builtin, and so does
    hooks/hook-meter-run.py for the gate itself, and PYTHONDONTWRITEBYTECODE
    plus a fresh sandbox means no cached bytecode skips the step.
    ops/gate-replay.py uses that to prove each helper module listed in the
    manifest was actually executed by a replayed gate.

It is a library file with no entrypoint, like the modules in lib/.
"""
import os

_EPOCH = os.environ.get("CARR_GATE_REPLAY_EPOCH")

if _EPOCH:
    import sys
    import time as _time
    # Capture real deadline clocks before replacing the application clock.
    # Blocking waits must not depend on replay clock reads.
    import subprocess as _subprocess
    import threading as _threading
    import queue as _queue
    import concurrent.futures._base as _futures_base
    from types import SimpleNamespace as _SimpleNamespace

    # Futures dereferences its time module during iteration instead of capturing
    # a function at import, as queue/threading/subprocess do. Give its deadlines
    # a private real clock; application imports still see the logical clock.
    setattr(_futures_base, "time", _SimpleNamespace(monotonic=_time.monotonic))

    _PINNED = float(_EPOCH)
    _elapsed = 0.0
    _real_sleep = _time.sleep
    _real_localtime = _time.localtime
    _real_gmtime = _time.gmtime
    _real_ctime = _time.ctime
    _real_strftime = _time.strftime

    def _monotonic():
        global _elapsed
        _elapsed += 0.001
        return _elapsed

    def _monotonic_ns():
        return int(_monotonic() * 1e9)

    def _sleep(seconds):
        global _elapsed
        _real_sleep(seconds)
        _elapsed += seconds

    def _now():
        return _PINNED + _monotonic()

    def _time_fn():
        return _now()

    def _time_ns_fn():
        return int(_now() * 1e9)

    def _localtime(secs=None):
        return _real_localtime(_now() if secs is None else secs)

    def _gmtime(secs=None):
        return _real_gmtime(_now() if secs is None else secs)

    def _ctime(secs=None):
        return _real_ctime(_now() if secs is None else secs)

    def _strftime(fmt, t=None):
        return _real_strftime(fmt, _real_localtime(_now()) if t is None else t)

    _time.time = _time_fn
    _time.time_ns = _time_ns_fn
    _time.monotonic = _monotonic
    _time.monotonic_ns = _monotonic_ns
    _time.sleep = _sleep
    _time.localtime = _localtime
    _time.gmtime = _gmtime
    _time.ctime = _ctime
    _time.strftime = _strftime

    def _patch_datetime(module):
        base_dt = module.datetime
        base_date = module.date

        class _PinnedDateTime(base_dt):
            @classmethod
            def now(cls, tz=None):
                return base_dt.fromtimestamp(_now(), tz)

            @classmethod
            def utcnow(cls):
                return base_dt.fromtimestamp(_now(), module.timezone.utc).replace(tzinfo=None)

            @classmethod
            def today(cls):
                return base_dt.fromtimestamp(_now())

        class _PinnedDate(base_date):
            @classmethod
            def today(cls):
                return base_date.fromtimestamp(_now())

        _PinnedDateTime.__name__ = "datetime"
        _PinnedDateTime.__qualname__ = "datetime"
        _PinnedDate.__name__ = "date"
        _PinnedDate.__qualname__ = "date"
        module.datetime = _PinnedDateTime
        module.date = _PinnedDate

    def _refuse(*_args, **_kwargs):
        raise OSError("gate-replay sandbox: network access is disabled")

    def _patch_socket(module):
        def _gai(*_args, **_kwargs):
            raise module.gaierror(-2, "gate-replay sandbox: name resolution is disabled")

        module.socket.connect = _refuse
        module.socket.connect_ex = _refuse
        module.create_connection = _refuse
        module.getaddrinfo = _gai
        module.gethostbyname = _gai
        module.gethostbyname_ex = _gai

    # datetime and socket may not be imported yet, and importing them here
    # would cost every gate process the import. Patch them the moment
    # something imports them instead.
    import importlib.abc
    import importlib.util

    _PATCHERS = {"datetime": _patch_datetime, "socket": _patch_socket}

    class _PatchingFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            patcher = _PATCHERS.get(fullname)
            if patcher is None:
                return None
            sys.meta_path.remove(self)
            try:
                spec = importlib.util.find_spec(fullname)
            finally:
                sys.meta_path.insert(0, self)
            if spec is None or spec.loader is None:
                return spec
            loader = spec.loader
            real_exec = loader.exec_module

            def exec_module(module, _real=real_exec, _patch=patcher):
                _real(module)
                _patch(module)

            try:
                loader.exec_module = exec_module  # type: ignore[method-assign]
            except (AttributeError, TypeError):
                return spec
            return spec

    for _name, _patcher in _PATCHERS.items():
        if _name in sys.modules:
            _patcher(sys.modules[_name])
    sys.meta_path.insert(0, _PatchingFinder())

    _ROOT = os.environ.get("CARR_GATE_REPLAY_ROOT", "")
    _TRACE = os.environ.get("CARR_GATE_REPLAY_TRACE", "")
    if _ROOT and _TRACE:
        _ROOT_SLASH = _ROOT.rstrip("/") + "/"
        _REAL_ROOT = os.path.realpath(_ROOT).rstrip("/") + "/"
        _opened: "set[str]" = set()

        def _audit(event, args):
            # "compile" fires when the import system (or hook-meter-run.py)
            # compiles a module's source, so it names exactly the Python files
            # the process EXECUTED. "open" would also name files a gate merely
            # reads, such as a path quoted in the command it is judging, and
            # that is not evidence the gate runs through a helper.
            if event != "compile" or len(args) < 2:
                return
            path = args[1]
            if not isinstance(path, str) or not path.endswith(".py"):
                return
            if path.startswith(_ROOT_SLASH):
                rel = path[len(_ROOT_SLASH):]
            elif path.startswith(_REAL_ROOT):
                rel = path[len(_REAL_ROOT):]
            else:
                return
            if rel.startswith(("out/", ".git/")) or "__pycache__" in rel:
                return
            _opened.add(rel)

        sys.addaudithook(_audit)

        def _flush():
            if not _opened:
                return
            try:
                line = "\n".join(sorted(_opened)) + "\n"
                fd = os.open(_TRACE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, line.encode("utf-8"))
                finally:
                    os.close(fd)
            except OSError:
                pass

        # hooks/hook-meter-run.py leaves through os._exit, which skips atexit,
        # so the flush rides on both doors.
        import atexit

        atexit.register(_flush)
        _real_exit = os._exit

        def _exit(code, _real=_real_exit):
            _flush()
            _real(code)

        os._exit = _exit

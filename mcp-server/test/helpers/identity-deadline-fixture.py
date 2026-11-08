"""Deliver a real deadline signal only after the owned process tree is ready."""
import importlib.util
import os
from pathlib import Path
import signal
import sys
import threading
from types import SimpleNamespace

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('proof', root / 'tools/prove-identity-domain-recorded-addresses.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
wrapper = Path(sys.argv[sys.argv.index('--call-wrapper') + 1]).resolve()
mode = wrapper.with_name('mode').read_text()
owned = []
clock = [0.0]
proof.time = SimpleNamespace(monotonic=lambda: clock[0])
proof.signal.setitimer = lambda *_args: None


def abort():
    for child in owned:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os._exit(124)


watchdog = threading.Timer(15, abort)
watchdog.start()
launch = proof.subprocess.Popen


def expire():
    clock[0] = 30.0
    signal.raise_signal(signal.SIGALRM)


def acquire(command, **kwargs):
    child = launch(command, **kwargs)
    owned.append(child)
    if Path(command[0]).resolve() != wrapper:
        return child
    assert child.stdout.readline() == b'ready\n', 'descendant readiness handshake missing'
    if mode == 'late-launch':
        wrapper.with_name('late.marker').write_text('acquired')
        expire()
    else:
        def communicate(**_kwargs):
            if mode == 'late-descendant':
                assert child.stdout.readline() == b'{}\n', 'partial output missing'
            expire()
        child.communicate = communicate
    return child


proof.subprocess.Popen = acquire
try:
    code = proof.main()
    assert all(child.poll() is not None for child in owned), 'proof returned with a live owned child'
finally:
    for child in owned:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
    watchdog.cancel()
sys.exit(code)

"""Own a proof's alarm lifetime inside its caller's result classifier."""
from contextlib import contextmanager
import signal


@contextmanager
def proof_deadline(seconds, unavailable):
    """Raise the proof's unavailable error on expiry; always restore its handler."""
    def expired(_signal, _frame):
        raise unavailable('deadline_exhausted')

    previous = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous)

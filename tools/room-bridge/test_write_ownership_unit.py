import sys, tempfile, subprocess, time, unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import write_ownership as w

class CheckoutWriters(unittest.TestCase):
    def test_writers_are_exclusive_only_in_the_same_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a',Path(tmp)/'b'
            for p in (a,b):
                subprocess.run(['git','init','-q',str(p)],check=True)
            with w.checkout_writer(str(a)):
                with self.assertRaises(w.DeskError):
                    with w.checkout_writer(str(a)): pass
                with w.checkout_writer(str(b)): pass
            with w.checkout_writer(str(a)): pass

class ProcessGroupRecovery(unittest.TestCase):
    def test_only_zombie_members_prove_group_termination(self):
        executor = {**w.process_owner(), 'pid': 12345, 'pgid': 12345,
                    'start_time': 'fixture', 'kind': 'process_group'}
        cases = [
            ('zombie', '12345 Z\n', True),
            ('zombie with state flags', '12345 Z+\n', True),
            ('unrelated live group', '9 R\n12345 Z\n', True),
            ('mixed live and zombie', '12345 Z\n12345 R\n', False),
            ('live group', '12345 S\n', False),
            ('empty inventory', '', False),
            ('missing group', '9 R\n', False),
            ('malformed inventory', '12345 Z\nincomplete\n', False),
            ('invalid group id', 'unknown Z\n12345 Z\n', False),
        ]
        for name, output, confirmed in cases:
            with self.subTest(name=name), \
                 patch.object(w.os, 'kill', side_effect=ProcessLookupError), \
                 patch.object(w.os, 'killpg'), \
                 patch.object(w.subprocess, 'run', return_value=subprocess.CompletedProcess(
                     ['/bin/ps'], 0, output, '')):
                evidence = w.termination_evidence({'executor': executor})
                self.assertEqual(evidence, 'process group terminated' if confirmed else None)

    def test_unavailable_inventory_never_proves_group_termination(self):
        executor = {**w.process_owner(), 'pid': 12345, 'pgid': 12345,
                    'start_time': 'fixture', 'kind': 'process_group'}
        for error in (OSError('unavailable'), subprocess.CalledProcessError(1, ['/bin/ps']),
                      subprocess.TimeoutExpired(['/bin/ps'], 2)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(w.os, 'kill', side_effect=ProcessLookupError), \
                 patch.object(w.os, 'killpg'), \
                 patch.object(w.subprocess, 'run', side_effect=error):
                self.assertIsNone(w.termination_evidence({'executor': executor}))

    def test_live_leader_never_uses_zombie_inventory_to_release_ownership(self):
        executor = {**w.process_owner(), 'pgid': 12345, 'kind': 'process_group'}
        with patch.object(w.subprocess, 'run') as inventory:
            self.assertIsNone(w.termination_evidence({'executor': executor}))
        inventory.assert_not_called()

    def test_native_zombie_group_is_terminated_before_its_parent_reaps_it(self):
        leader = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5)'],
                                  process_group=0)
        child = None
        try:
            executor = {**w.process_owner(), 'pid': leader.pid, 'pgid': leader.pid,
                        'start_time': w.process_start(leader.pid), 'kind': 'process_group'}
            child = subprocess.Popen([sys.executable, '-c', 'pass'], process_group=leader.pid)
            leader.wait(timeout=3)
            deadline = time.monotonic() + 2
            while True:
                inventory = subprocess.run(['/bin/ps', '-axo', 'pgid=,stat='], check=True,
                                           capture_output=True, text=True, timeout=2)
                states = [line.split()[1] for line in inventory.stdout.splitlines()
                          if line.split()[0] == str(leader.pid)]
                if states and all(state.startswith('Z') for state in states):
                    break
                if time.monotonic() >= deadline:
                    self.fail(f'Expected an unreaped zombie group, observed {states}')
                time.sleep(0.01)
            self.assertEqual(w.termination_evidence({'executor': executor}),
                             'process group terminated')
        finally:
            leader.wait(timeout=3)
            if child is not None:
                child.wait(timeout=3)


if __name__=='__main__': unittest.main()

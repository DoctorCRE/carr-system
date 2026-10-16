import sys, tempfile, subprocess, unittest
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

if __name__=='__main__': unittest.main()

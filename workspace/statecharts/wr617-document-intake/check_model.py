"""Check the intake safety rules and prove each detects its corresponding fault."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RULES = {
    'VerifiedBeforeFiled': 'UnverifiedFile',
    'OneAttachment': 'DuplicateAttachment',
    'VisibleAccessOnly': 'InvisibleAccess',
    'TerminalCannotFinalize': 'TerminalFinalize',
    'DocumentNeverPublishable': 'PublishedDocument',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', required=True, type=Path)
    args = parser.parse_args()
    jar = args.jar.resolve()
    source = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix='document-intake-tlc-') as scratch:
        root = Path(scratch)
        shutil.copy(source / 'DocumentIntake.tla', root)
        shutil.copy(source / 'DocumentIntake.cfg', root)
        for rule, mutation in [('all', 'none'), *RULES.items()]:
            config = root / 'DocumentIntake.cfg'
            if rule != 'all':
                config.write_text(f'SPECIFICATION Spec\nCONSTANT Mutation = "{mutation}"\n'
                                  f'INVARIANT TypeOK\nINVARIANT {rule}\nCHECK_DEADLOCK FALSE\n')
            result = subprocess.run([os.environ.get('JAVA', 'java'), '-Xmx256m', '-XX:+UseParallelGC', '-cp', str(jar),
                                     'tlc2.TLC', '-workers', '1', '-config', str(config), 'DocumentIntake'],
                                    cwd=root, capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
            if rule == 'all':
                assert result.returncode == 0 and 'No error has been found' in output, output
                print('PASS TypeOK and ' + ', '.join(RULES))
                for line in output.splitlines():
                    if 'states generated' in line or 'depth of' in line:
                        print(line)
            else:
                assert result.returncode != 0 and f'Invariant {rule} is violated' in output, output
                print(f'CAUGHT {mutation}: Invariant {rule} is violated')


if __name__ == '__main__':
    main()

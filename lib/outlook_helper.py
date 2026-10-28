"""Build and sign the configured Outlook copy helper before publishing it."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from lib.outlook_settings import OutlookSettings, SettingsError


def install_helper(settings: OutlookSettings) -> None:
    repo = settings.repo
    source = repo / 'tools/outlook-intake-copy.c'
    helper = settings.copy_helper
    work_root = (settings.repo / 'out/outlook-intake').resolve()
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
             f'-DOUTLOOK_PROFILE={json.dumps(str(settings.profile))}',
             f'-DOUTLOOK_WORK_ROOT={json.dumps(str(work_root))}',
             f'-DOUTLOOK_REPO={json.dumps(str(settings.repo))}']
    pin = helper.with_suffix('.pin')
    wanted = hashlib.sha256(source.read_bytes() + json.dumps([flags, settings.signing_identity]).encode()).hexdigest()
    helper.parent.mkdir(parents=True, exist_ok=True)
    if not (helper.is_file() and os.access(helper, os.X_OK) and pin.is_file() and pin.read_text() == wanted):
        with tempfile.TemporaryDirectory(dir=helper.parent) as temp:
            built = Path(temp) / helper.name
            subprocess.run(['/usr/bin/cc', *flags, str(source), '-o', str(built)], check=True)
            built.chmod(0o700)
            identity = settings.signing_identity or '-'
            try:
                subprocess.run(['codesign', '--force', '-s', identity, '--identifier', 'outlook-intake-copy', str(built)],
                               check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as error:
                raise SettingsError(f'cannot sign Outlook helper with {identity!r}; '
                                    f'check that the code-signing identity is in this Mac keychain: {error.stderr}') from error
            if settings.signing_identity:
                requirement = subprocess.run(['codesign', '-d', '-r-', str(built)],
                                             check=True, capture_output=True, text=True)
                designated = [line for line in (requirement.stdout + requirement.stderr).splitlines()
                              if line.startswith('designated =>')]
                if (len(designated) != 1 or 'certificate' not in designated[0]
                        or 'cdhash' in designated[0]):
                    raise SettingsError('Outlook helper signing must produce a certificate designated requirement, not a cdhash')
            os.replace(built, helper)
            if not settings.signing_identity:
                print(f'install-outlook-intake: ad-hoc helper rebuilds need a Full Disk Access re-grant for {helper}', flush=True)
        pin.write_text(wanted)
    settings.verify_helper()

"""Build and sign the configured Outlook copy helper before publishing it."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from lib import helper_signing
from lib.outlook_settings import OutlookSettings, SettingsError

IDENTIFIER = 'com.carr.outlook-intake-copy'


def _regrant_notice(helper: Path) -> str:
    return (f'install-outlook-intake: helper replacements need a Full Disk Access re-grant for {helper}\n'
            'All re-grant steps are required:\n'
            '  1. Open System Settings > Privacy & Security > Full Disk Access.\n'
            f'  2. Remove the old entry and add {helper} again, with access enabled.\n'
            '  3. After re-granting access, run the launch commands printed by the installer.\n'
            '  4. Check that the next scheduled intake run succeeds.')


def install_helper(settings: OutlookSettings, *, rebuild_helper: bool = False) -> None:
    repo = settings.repo
    source = repo / 'tools/outlook-intake-copy.c'
    helper = settings.copy_helper
    work_root = (settings.repo / 'out/outlook-intake').resolve()
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
             f'-DOUTLOOK_PROFILE={json.dumps(str(settings.profile))}',
             f'-DOUTLOOK_WORK_ROOT={json.dumps(str(work_root))}',
             f'-DOUTLOOK_REPO={json.dumps(str(settings.repo))}']
    pin = helper.with_suffix('.pin')
    try:
        sha1 = helper_signing.certificate_sha1()
    except helper_signing.SigningError as error:
        raise SettingsError(str(error)) from error
    if not sha1:
        print(helper_signing.FALLBACK, file=sys.stderr, flush=True)
    # The '' keeps ad-hoc pins written before the stable identity valid.
    signer = [flags, ''] + ([IDENTIFIER, sha1] if sha1 else [])
    wanted = hashlib.sha256(source.read_bytes() + json.dumps(signer).encode()).hexdigest()
    helper.parent.mkdir(parents=True, exist_ok=True)
    if not (helper.is_file() and os.access(helper, os.X_OK) and pin.is_file() and pin.read_text() == wanted):
        try:
            regrant = not sha1 or (helper.exists() and not helper_signing.has_identity(helper, IDENTIFIER, sha1))
        except helper_signing.SigningError as error:
            raise SettingsError(str(error)) from error
        if helper.exists() and not rebuild_helper and regrant:
            raise SettingsError(f'refusing to replace Outlook helper {helper}; '
                                'rerun bin/install-outlook-intake.sh --rebuild-helper explicitly.\n'
                                + _regrant_notice(helper))
        with tempfile.TemporaryDirectory(dir=helper.parent) as temp:
            built = Path(temp) / helper.name
            subprocess.run(['/usr/bin/cc', *flags, str(source), '-o', str(built)], check=True)
            built.chmod(0o700)
            if sha1:
                try:
                    helper_signing.sign(built, IDENTIFIER, sha1)
                except helper_signing.SigningError as error:
                    raise SettingsError(f'cannot sign Outlook helper with {helper_signing.IDENTITY!r}: {error}') from error
            else:
                try:
                    subprocess.run(['codesign', '--force', '-s', '-', '--identifier', 'outlook-intake-copy', str(built)],
                                   check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as error:
                    raise SettingsError("cannot sign Outlook helper with '-'; "
                                        f'check that the code-signing identity is in this Mac keychain: {error.stderr}') from error
            os.replace(built, helper)
            if regrant:
                print(_regrant_notice(helper), flush=True)
        pin.write_text(wanted)
    settings.verify_helper()

"""Operational adapter for the reviewed tripwire. No installer or live configuration."""
import hashlib
import json
from pathlib import Path
import plistlib
import runpy
import subprocess
import tempfile

from lib.outlook_intake import OUT, WORK_PREFIX, _snapshot
from lib.outlook_version_tripwire import IntakeBlocked, _digest, _version

ROOT = Path(__file__).resolve().parents[1]


def installed_version(app):
    with (Path(app) / 'Contents' / 'Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    return _version({'version': info['CFBundleShortVersionString'], 'build': info['CFBundleVersion']})


def source_binding(observer, expected_sha256):
    observer = Path(observer)
    actual = hashlib.sha256(observer.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise IntakeBlocked('observer_pin_mismatch')
    paths = ['spec/outlook/hxstore-atlas.json', 'spec/outlook/hxstore-harness.json',
             'lib/outlook_version_tripwire.py', 'lib/outlook_version_ports.py', 'lib/outlook_intake.py',
             'ops/hxstore-atlas.py']
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / 'vendor/hxprobe/src').rglob('*.rs'))]
    sources = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    manifest = {'schema': 'outlook-version-source/v1', 'observer_sha256': actual, 'sources': sources}
    return _digest(manifest), manifest


def known_record_types():
    atlas = json.loads((ROOT / 'spec/outlook/hxstore-atlas.json').read_text())
    runpy.run_path(str(ROOT / 'ops/hxstore-atlas.py'))['validate'](atlas)
    if atlas['versioned_map']['schema'] != 'outlook-local-map-scope/v1':
        raise IntakeBlocked('atlas_scope_mismatch')
    return {'mail'} | {e['id'] for e in atlas['entries']
                       if e['identifier']['kind'] == 'record_type' and e['confidence'] != 'unknown'}


class OperationalPorts:
    def __init__(self, home, app, observer, expected_sha256):
        self.home, self.app, self.observer = Path(home), Path(app), Path(observer)
        self.expected_sha256 = expected_sha256
        self.version = installed_version(app)
        self.source_pin, self.manifest = source_binding(observer, expected_sha256)

    def _unchanged(self):
        if installed_version(self.app) != self.version:
            raise IntakeBlocked('version_changed_during_observation')
        if source_binding(self.observer, self.expected_sha256)[0] != self.source_pin:
            raise IntakeBlocked('source_changed_during_observation')

    def snapshot(self, work):
        self._unchanged()
        out = ROOT / OUT
        out.mkdir(parents=True, mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=WORK_PREFIX, dir=out) as staged:
            _snapshot(self.home, Path(staged))
            self._unchanged()
            (Path(staged) / 'store.hxd').replace(work / 'store.hxd')

    def observe(self, copy):
        self._unchanged()
        before = copy.stat()
        done = subprocess.run([str(self.observer), 'observe', str(copy)], capture_output=True, timeout=900)
        after = copy.stat()
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if done.returncode or done.stderr or any(getattr(before, k) != getattr(after, k) for k in fields):
            raise IntakeBlocked('observer_failed_or_copy_changed')
        self._unchanged()
        return json.loads(done.stdout)

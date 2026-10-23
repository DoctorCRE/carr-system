"""Defer derived bytes to regen while merging the registry's preserved metadata."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile


OPERATIONS = re.compile(r'export const SCAC_MUTATION_OPERATIONS = Object.freeze\(\{[\s\S]*?\n\}\);')


def main():
    base, ours, theirs, path = sys.argv[1:]
    if path == 'db/schema.sql':
        return 0
    if path != 'mcp-server/src/scac-mutation-registry.current.generated.js':
        return 1
    with tempfile.TemporaryDirectory(prefix='carr-generated-merge-') as directory:
        files = []
        for index, source in enumerate([ours, base, theirs]):
            content, count = OPERATIONS.subn(
                'export const SCAC_MUTATION_OPERATIONS = Object.freeze({\n});',
                Path(source).read_text())
            if count != 1:
                return 1
            target = Path(directory) / str(index)
            target.write_text(content)
            files.append(str(target))
        result = subprocess.run(['git', 'merge-file', '-p', *files], capture_output=True)
        if result.returncode == 0:
            Path(ours).write_bytes(result.stdout)
        return int(result.returncode != 0)


if __name__ == '__main__':
    raise SystemExit(main())

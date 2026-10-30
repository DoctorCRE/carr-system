"""Read scanned PDF regions with installed Apple Vision, without a model or network."""

import json
from pathlib import Path
import subprocess
import tempfile


def ocr_regions(data):
    with tempfile.TemporaryDirectory(prefix="deal-vision-") as folder:
        source = Path(folder) / "source.pdf"
        source.write_bytes(data)
        result = subprocess.run(
            ["/usr/bin/swift", str(Path(__file__).with_suffix(".swift")), str(source)],
            capture_output=True,
            timeout=180,
        )
        if result.returncode:
            raise RuntimeError("local Apple Vision recognition failed")
        return json.loads(result.stdout)

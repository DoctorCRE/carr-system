"""Release fixture admission for Python proof tests, using the reader marker contract."""
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path

MARKER = json.loads((Path(__file__).resolve().parents[1] / 'lib/release-reader-marker.json').read_text())


class ReleaseFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get_all(MARKER['header']) != [MARKER['value']]:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(MARKER['refusal'].encode())
            return
        self.release_GET()

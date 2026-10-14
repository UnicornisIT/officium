"""Loopback stand-in for GET /repos/<owner>/<name>/releases/latest (CI only).

    python3 qa_release_api.py LATEST_TAG_FILE PORT

The tag is read from LATEST_TAG_FILE on every request, so a test publishes
a "new release" by writing that file.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


LATEST_FILE = Path(sys.argv[1])


class ReleaseHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        if not self.path.rstrip('/').endswith('/releases/latest'):
            self.send_error(404)
            return
        tag = LATEST_FILE.read_text(encoding='utf-8').strip()
        body = json.dumps({
            'tag_name': tag, 'name': f'QA {tag}', 'body': '## QA release',
            'published_at': '2026-10-14T00:00:00Z',
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', int(sys.argv[2])), ReleaseHandler).serve_forever()

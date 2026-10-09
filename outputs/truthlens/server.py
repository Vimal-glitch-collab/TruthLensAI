"""Local-only HTTP API. Uploaded media is temporary; reports live in SQLite."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from truthlens.analysis import AnalysisError, image_report, news_report, video_report

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('TRUTHLENS_DATA_DIR', ROOT / 'data'))
MAX_BYTES = 50 * 1024 * 1024
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
SLOTS = threading.BoundedSemaphore(4)
LOG = logging.getLogger('truthlens')


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / 'reports.sqlite3', timeout=10)
    try:
        with connection:
            connection.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
            yield connection
    finally:
        connection.close()


def save(job_id, status, payload, create=False):
    with database() as db:
        if create:
            db.execute('INSERT INTO reports VALUES (?, ?, ?, ?)', (job_id, time.time(), status, json.dumps(payload, allow_nan=False)))
            # Keep only the most recent 200 finished reports; active jobs are never removed.
            db.execute("DELETE FROM reports WHERE status IN ('complete','failed') AND id NOT IN (SELECT id FROM reports ORDER BY created DESC LIMIT 200)")
        else:
            db.execute('UPDATE reports SET status=?, payload=? WHERE id=?', (status, json.dumps(payload, allow_nan=False), job_id))


def process(job_id, kind, name, data):
    try:
        save(job_id, 'processing', {'name': name, 'kind': kind})
        if kind == 'news':
            report = news_report(data['text'], name, data.get('source_url', ''))
        elif kind == 'image':
            report = image_report(data, name)
        else:
            suffix = Path(name).suffix.lower()
            if suffix not in {'.mp4', '.mov', '.webm', '.avi'}:
                raise AnalysisError('Use MP4, MOV, WebM, or AVI video.')
            with tempfile.TemporaryDirectory(prefix='truthlens-') as directory:
                path = Path(directory) / ('upload' + suffix)
                path.write_bytes(data)
                report = video_report(path, name, hashlib.sha256(data).hexdigest())
        report['id'] = job_id
        save(job_id, 'complete', report)
    except AnalysisError as exc:
        save(job_id, 'failed', {'name': name, 'kind': kind, 'error': str(exc)})
    except Exception:
        LOG.exception('Analysis failed for %s', job_id)
        save(job_id, 'failed', {'name': name, 'kind': kind, 'error': 'Analysis failed. Check the server log and any configured model contract.'})
    finally:
        SLOTS.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        LOG.info(format, *args)

    def respond(self, status, payload, mime='application/json', attachment=None):
        body = json.dumps(payload, allow_nan=False).encode() if mime == 'application/json' else payload
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        return host in allowed and (not origin or origin in {f'http://{value}' for value in allowed})

    def do_GET(self):
        if not self.trusted_request():
            return self.respond(403, {'error': 'Local requests only.'})
        path = urlparse(self.path).path
        if path == '/api/health':
            import importlib.util
            return self.respond(200, {'status': 'ok', 'video': bool(importlib.util.find_spec('cv2')), 'feature_model': Path(os.environ.get('TRUTHLENS_FEATURE_MODEL', ROOT / 'models/feature-model.json')).is_file(), 'onnx_configured': bool(os.environ.get('TRUTHLENS_ONNX_CONFIG'))})
        if path == '/api/reports':
            with database() as db:
                rows = db.execute('SELECT id, created, status, payload FROM reports ORDER BY created DESC LIMIT 200').fetchall()
            return self.respond(200, [{'id': row[0], 'created': row[1], 'status': row[2], 'name': json.loads(row[3]).get('name', ''), 'kind': json.loads(row[3]).get('kind', '')} for row in rows])
        if path.startswith('/api/reports/'):
            parts = path.split('/')
            download = len(parts) == 5 and parts[4] == 'download'
            job_id = parts[3] if len(parts) >= 4 else ''
            if not re.fullmatch('[a-f0-9]{32}', job_id) or (len(parts) != 4 and not download):
                return self.respond(404, {'error': 'Report not found.'})
            with database() as db:
                row = db.execute('SELECT status, payload FROM reports WHERE id=?', (job_id,)).fetchone()
            if download and row:
                if row[0] != 'complete':
                    return self.respond(409, {'error': 'Only completed reports can be exported.'})
                return self.respond(200, json.loads(row[1]), attachment=f'truthlens-{job_id}.json')
            return self.respond(200, {'status': row[0], 'report': json.loads(row[1])}) if row else self.respond(404, {'error': 'Report not found.'})
        assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/favicon.svg': 'favicon.svg'}
        if path in assets:
            name = assets[path]
            mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8', 'svg': 'image/svg+xml'}[name.rsplit('.', 1)[-1]]
            return self.respond(200, (ROOT / 'web' / name).read_bytes(), mime)
        self.respond(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.trusted_request():
            return self.respond(403, {'error': 'Local requests only.'})
        parsed = urlparse(self.path)
        kind = parsed.path.rsplit('/', 1)[-1]
        if parsed.path not in {'/api/analyze/image', '/api/analyze/video', '/api/analyze/news'}:
            return self.respond(404, {'error': 'Not found.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.respond(400, {'error': 'Invalid content length.'})
        if self.headers.get('Transfer-Encoding') or size <= 0 or size > MAX_BYTES or (kind == 'news' and size > 220_000):
            self.close_connection = True
            return self.respond(413, {'error': 'Empty or oversized upload. Media limit: 50 MB.'})
        expected = 'application/json' if kind == 'news' else 'application/octet-stream'
        if self.headers.get('Content-Type', '').split(';')[0] != expected:
            self.close_connection = True
            return self.respond(415, {'error': f'Expected {expected}.'})
        if not SLOTS.acquire(blocking=False):
            self.close_connection = True
            return self.respond(429, {'error': 'The analysis queue is full. Try again shortly.'})
        handed_off = False
        try:
            data = self.rfile.read(size)
            if len(data) != size:
                raise AnalysisError('Upload was interrupted.')
            name = parse_qs(parsed.query).get('name', [f'{kind} upload'])[0][:160]
            if kind == 'news':
                try:
                    data = json.loads(data)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise AnalysisError('Invalid JSON article.') from exc
                if not isinstance(data, dict) or not isinstance(data.get('text'), str) or not isinstance(data.get('source_url', ''), str):
                    raise AnalysisError('Article text and source URL must be strings.')
                if not 40 <= len(data['text'].strip()) <= 50_000:
                    raise AnalysisError('Paste between 40 and 50,000 characters of article text.')
            job_id = uuid.uuid4().hex
            save(job_id, 'queued', {'name': name, 'kind': kind}, create=True)
            POOL.submit(process, job_id, kind, name, data)
            handed_off = True
            self.respond(202, {'id': job_id, 'status': 'queued'})
        except AnalysisError as exc:
            self.respond(400, {'error': str(exc)})
        except (TimeoutError, OSError):
            self.close_connection = True
        except Exception:
            LOG.exception('Request failed')
            self.respond(500, {'error': 'Unable to start analysis.'})
        finally:
            if not handed_off:
                SLOTS.release()

    def do_DELETE(self):
        if not self.trusted_request() or self.headers.get('X-TruthLens-Action') != 'delete':
            return self.respond(403, {'error': 'Request rejected.'})
        path = urlparse(self.path).path
        if not path.startswith('/api/reports/'):
            return self.respond(404, {'error': 'Not found.'})
        with database() as db:
            row = db.execute('SELECT status FROM reports WHERE id=?', (path.rsplit('/', 1)[-1],)).fetchone()
            if not row:
                return self.respond(404, {'error': 'Report not found.'})
            if row[0] in ('queued', 'processing'):
                return self.respond(409, {'error': 'Wait for analysis to finish before deleting.'})
            db.execute('DELETE FROM reports WHERE id=?', (path.rsplit('/', 1)[-1],))
        self.respond(200, {'deleted': True})


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    with database() as db:
        db.execute("UPDATE reports SET status='failed', payload=json_set(payload, '$.error', 'Server restarted before analysis completed.') WHERE status IN ('queued', 'processing')")
    port = int(os.environ.get('PORT', '8000'))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    LOG.info('TruthLens is ready at http://127.0.0.1:%s', port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        POOL.shutdown(wait=True)


if __name__ == '__main__':
    main()

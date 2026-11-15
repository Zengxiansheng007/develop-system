"""Synthetic loopback/SQLite service; region is an explicitly unrelated fake boundary."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

database, discovery, instance, mode = sys.argv[1:]
source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
print(os.environ.get('HTTP_TEST_SECRET', 'synthetic fixture startup'), flush=True)
print(os.environ.get('HTTP_TEST_SECRET', 'synthetic fixture stderr'), file=sys.stderr, flush=True)
if mode == 'exit':
    raise SystemExit(3)
with closing(sqlite3.connect(database)) as db:
    db.execute('CREATE TABLE items(name TEXT)')
    db.commit()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Set-Cookie', 'private=session-test-marker')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/sensitive':
            return self.reply(200, {'auth_token': 'synthetic-auth-token-marker'})
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/items')
            self.end_headers()
            return
        if self.path == '/slow':
            self.send_response(200)
            self.end_headers()
            for _ in range(80):
                self.wfile.write(b' ')
                self.wfile.flush()
                time.sleep(0.1)
            return
        if self.path == '/identity':
            return self.reply(200, {'source_sha256': source, 'instance_id': instance,
                                   'pid': os.getpid(), 'database': str(Path(database).resolve()),
                                   'customer_notes': 'unselected-identity-marker',
                                   'sessionToken': 'synthetic-session-marker'})
        if self.path == '/private-health':
            return self.reply(200, {'status': 'ready', 'customer_notes': 'unselected-readiness-marker'})
        if self.path == '/health':
            return self.reply(503 if mode == 'unready' else 200, {'status': 'ready'})
        with closing(sqlite3.connect(database)) as db:
            rows = db.execute('SELECT name FROM items').fetchall()
        self.reply(200, {'names': [r[0] for r in rows], 'region': 'test',
                         'nested': {'password': 'body-secret-marker'}})

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/conflict':
            with closing(sqlite3.connect(database)) as db:
                key = db.execute('SELECT rowid FROM items ORDER BY rowid LIMIT 1').fetchone()[0]
            return self.reply(409, {'error': 'exists', 'id': key})
        with closing(sqlite3.connect(database)) as db:
            cursor = db.execute('INSERT INTO items VALUES(?)', ('wrong' if mode == 'wrong' else value['name'],))
            key = cursor.lastrowid
            db.commit()
        if mode == 'delay':
            time.sleep(8)
        if mode == 'cleanup':
            Path('residual').mkdir()
        self.reply(200, {'id': key, 'name': value['name'], 'region': 'test'})

    def do_DELETE(self):
        key = int(self.path.rsplit('/', 1)[1])
        with closing(sqlite3.connect(database)) as db:
            db.execute('DELETE FROM items WHERE rowid=?', (key,))
            db.commit()
        self.reply(200, {'deleted': key})


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
Path(discovery).write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
server.serve_forever()

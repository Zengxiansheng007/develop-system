"""Synthetic async jobs: real loopback, SQLite and delayed worker completion."""
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

database, discovery, instance, mode = sys.argv[1:]
source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
mode_fields = mode.split('-')
correlation_fields = mode_fields if len(mode_fields) >= 2 and all(f in ('id', 'token') for f in mode_fields[:2]) else None
correlation_value = '1' if mode.endswith('-short') else 'synthetic-job-token-28'
with closing(sqlite3.connect(database)) as db:
    db.execute('CREATE TABLE jobs(state TEXT, total INTEGER, error TEXT)')
    db.commit()


def finish(key, amounts):
    time.sleep(0.4)
    if mode == 'stuck':
        return
    with closing(sqlite3.connect(database)) as db:
        state = 'failed' if mode == 'failure' else 'succeeded'
        total = -1 if mode == 'wrong_total' else sum(amounts)
        db.execute('UPDATE jobs SET state=?,total=?,error=? WHERE rowid=?',
                   (state, total, 'synthetic failure' if mode == 'failure' else None, key))
        db.commit()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        self.send_response(status)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with closing(sqlite3.connect(database)) as db:
            key = db.execute("INSERT INTO jobs VALUES('pending',NULL,NULL)").lastrowid
            db.commit()
        threading.Thread(target=finish, args=(key, data['amounts']), daemon=True).start()
        if mode == 'write_timeout':
            time.sleep(8)
        if mode == 'missing_id':
            return self.reply(202, {'state': 'pending'})
        if correlation_fields:
            if mode.endswith('-acceptedarray'):
                return self.reply(202, {'rows': [{correlation_fields[0]: correlation_value}], 'state': 'pending'})
            return self.reply(202, {correlation_fields[0]: correlation_value, 'state': 'pending'})
        self.reply(202, {'id': key, 'state': 'pending'})

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, {'status': 'ready'})
        if self.path == '/identity':
            return self.reply(200, {'source_sha256': source, 'instance_id': instance,
                                   'pid': os.getpid(), 'database': str(Path(database).resolve())})
        with closing(sqlite3.connect(database)) as db:
            if self.path == '/jobs':
                return self.reply(200, {'count': db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]})
            if correlation_fields:
                if self.path != '/jobs/' + correlation_value:
                    return self.reply(404, {'error': 'wrong correlation path'})
                key = 1
            else:
                key = int(self.path.rsplit('/', 1)[1])
            row = db.execute('SELECT state,total,error FROM jobs WHERE rowid=?', (key,)).fetchone()
        if mode == 'slow':
            time.sleep(8)
        field = correlation_fields[1] if correlation_fields else 'id'
        identifier = correlation_value if correlation_fields else key + 1 if mode == 'wrong_job' else key
        if mode.endswith('-responsearray'):
            return self.reply(200, {'rows': [{field: identifier}], 'state': row[0], 'states': [row[0]], 'total': row[1]})
        self.reply(200, {field: identifier,
                         'state': row[0], 'total': row[1], 'error': row[2],
                         'trace': self.headers.get('Authorization')})


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
Path(discovery).write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
server.serve_forever()

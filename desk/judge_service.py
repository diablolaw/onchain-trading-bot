"""Authenticated loopback Jev proxy. No wallet or execution imports."""
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from .ai import AIProviders,AIPolicy
from .post_questions import questions

CONFIG=Path(__file__).resolve().parent.parent/'data/judge-secret.json'

def setup(config=None):
    config=CONFIG if config is None else config
    if config.exists():
        if config.stat().st_mode & 0o077:raise ValueError('judge secret must be owner-only')
        data=json.loads(config.read_text())
        if not isinstance(data.get('secret'),str) or len(data['secret'])!=64:raise ValueError('invalid desk secret')
        return data
    config.parent.mkdir(parents=True,exist_ok=True)
    data=dict(secret=secrets.token_hex(32))
    fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(data,f)
    return data


def serve(port=8080,db_path="data/post-paper.sqlite",social_only=False):
    secret=setup(CONFIG.with_name('social-secret.json') if social_only else CONFIG)['secret']
    if not social_only and not os.environ.get('TYPESAFE_API_KEY'):raise ValueError('configure TYPESAFE_API_KEY in .env')
    lock=threading.Lock();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,data):
            raw=json.dumps(data,allow_nan=False).encode();self.send_response(code)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+secret):return self.reply(401,dict(error='unauthorized'))
            if self.path!='/social/jobs':return self.reply(404,dict(error='not_found'))
            import sqlite3
            from .social import jobs
            # A busy database must answer, not drop the connection: the tunnel reports a dropped one as the service being down.
            try:
                with sqlite3.connect(db_path,timeout=5) as db:found=jobs(db)
            except sqlite3.Error:return self.reply(503,dict(error='database_busy'))
            return self.reply(200,dict(jobs=found))
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+secret):return self.reply(401,dict(error='unauthorized'))
            if social_only and self.path!='/social':return self.reply(404,dict(error='not_found'))
            if self.path not in ('/judge','/social'):return self.reply(404,dict(error='not_found'))
            self.connection.settimeout(5)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=32000:raise ValueError()
                ask=json.loads(self.rfile.read(length))
                if not isinstance(ask,dict):raise ValueError()
                if self.path=='/social':
                    import sqlite3
                    from .social import submit
                    try:
                        with sqlite3.connect(db_path,timeout=5) as db:accepted=submit(db,ask)
                    except sqlite3.Error:return self.reply(503,dict(error='database_busy'))
                    return self.reply(200,accepted)
                if not isinstance(ask,dict) or set(ask)!={'question_set','state'} or not isinstance(ask['state'],dict):raise ValueError()
                qs=questions(ask['question_set'],ask['state'])
            except (ValueError,TypeError,KeyError):return self.reply(422,dict(error='invalid_question_or_state'))
            if not lock.acquire(blocking=False):return self.reply(429,dict(error='judge_busy'))
            try:
                now=time.monotonic();calls[:]=[t for t in calls if now-t<3600]
                if len(calls)>=60:return self.reply(429,dict(error='hourly_call_budget'))
                calls.append(now)
                provider=AIProviders(lambda *args:None,AIPolicy(max_calls=1))
                answer=provider.judge(ask['state'],qs)
                return self.reply(200,answer)
            except Exception as error:return self.reply(502,dict(error='jev_request_failed',error_type=type(error).__name__))
            finally:lock.release()
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(('Social evidence service' if social_only else 'Jev judge')+' listening on 127.0.0.1:'+str(port)+'; no trading tools.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return dict(stopped=True)

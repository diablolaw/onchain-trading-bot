"""Local authenticated market-only bridge. Browser credentials never reach Python."""
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from .core import number
from .onchain import address

CONFIG=Path(__file__).resolve().parent.parent/'data/fomo-bridge.json'
PORT=8766


def setup(path=CONFIG):
    path=Path(path)
    if path.exists():
        if path.stat().st_mode & 0o077: raise ValueError('FOMO bridge configuration must be owner-only')
        config=json.loads(path.read_text())
        if not isinstance(config.get('key'),str) or len(config['key'])!=64: raise ValueError('invalid bridge key')
        return config
    path.parent.mkdir(parents=True,exist_ok=True)
    config=dict(key=secrets.token_hex(32),port=PORT)
    import os
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f: json.dump(config,f)
    return config


def parse_metrics(token,row,stamp):
    if not isinstance(row,dict): raise ValueError('invalid FOMO token row')
    # Explicit identity, never positional pairing of responses.
    raw=row.get('address') or row.get('tokenAddress') or row.get('contractAddress')
    if raw!=token: raise ValueError('FOMO token identity mismatch')
    net=row.get('networkId',row.get('netId'))
    if str(net)!='1399811149': raise ValueError('FOMO network mismatch')
    def metric(*names):
        for name in names:
            if name in row:
                if isinstance(row[name],bool): return None
                return number(row[name])
        return None
    created=metric('createdAt','created')
    if created and created>1e11:created/=1000
    if not created or created>stamp:created=None
    count=metric('holders','holderCount')
    if count is not None and count!=int(count):raise ValueError('invalid FOMO holder count')
    return dict(token=token,observed_at=stamp,source='fomo:filterTokens',
        price=metric('priceUSD','priceUsd','price'),liquidity=metric('liquidity','liq'),
        market_cap=metric('marketCap','mcap'),volume_24h=metric('volume24','volume24h','vol24'),
        created_at=created,holder_count=int(count) if count is not None else None)


class Bridge:
    def __init__(self,config=None):
        self.config=config or setup();self.lock=threading.Lock();self.jobs=[];self.pending={};self.last_seen=0;self.session_ready=False;self.diagnostics={}
        bridge=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,value):
                raw=json.dumps(value,allow_nan=False).encode();self.send_response(status)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
            def authorized(self):
                return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+bridge.config['key'])
            def do_GET(self):
                if not self.authorized():return self.reply(401,dict(error='unauthorized'))
                if self.path=='/status':return self.reply(200,bridge.status())
                if self.path!='/jobs':return self.reply(404,dict(error='not_found'))
                with bridge.lock:
                    bridge.last_seen=time.time();jobs=bridge.jobs[:];bridge.jobs.clear()
                self.reply(200,dict(jobs=jobs))
            def do_POST(self):
                if not self.authorized():return self.reply(401,dict(error='unauthorized'))
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=262144:raise ValueError()
                    data=json.loads(self.rfile.read(length))
                    if self.path=='/heartbeat':
                        with bridge.lock:
                            bridge.last_seen=time.time();bridge.session_ready=data.get('session_ready') is True
                            raw=data.get('diagnostics',{})
                            if isinstance(raw,dict):
                                bridge.diagnostics={k:raw.get(k) is True for k in ('matching_origin','authorization_header_seen','bearer_header_seen','cookie_session_seen')}
                                count=raw.get('requests_seen',0)
                                bridge.diagnostics['requests_seen']=count if isinstance(count,int) and not isinstance(count,bool) and 0<=count<=1000000 else 0
                    elif self.path=='/result':
                        with bridge.lock:
                            item=bridge.pending.get(data.get('id'))
                            if item is None:raise ValueError()
                            item['result']=data;item['event'].set()
                    else:return self.reply(404,dict(error='not_found'))
                    self.reply(200,dict(ok=True))
                except (ValueError,TypeError,KeyError):self.reply(400,dict(error='invalid_request'))
        self.server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def start(self):self.thread.start();return self
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def status(self):
        return dict(connected=time.time()-self.last_seen<65,session_ready=self.session_ready,market_only=True,diagnostics=self.diagnostics)
    def tokens(self,tokens,timeout=90):
        tokens=list(dict.fromkeys(address(t) for t in tokens))
        output={};items=[]
        with self.lock:
            for offset in range(0,len(tokens),20):
                batch=tokens[offset:offset+20];job_id=secrets.token_hex(16)
                item=dict(event=threading.Event(),result=None)
                self.pending[job_id]=item;self.jobs.append(dict(id=job_id,tokens=batch))
                items.append((job_id,batch,item))
        deadline=time.monotonic()+timeout
        try:
            for job_id,batch,item in items:
                if not item['event'].wait(max(0,deadline-time.monotonic())):
                    raise RuntimeError('FOMO browser bridge timed out; check extension and session')
                result=item['result']
                if result.get('error'):
                    code=result['error']
                    allowed={'session_not_observed_reload_fomo','session_expired_reload_fomo','invalid_job','unsupported_response_shape','response_identity_mismatch','response_too_large','market_request_failed','fomo_api_rejected'}
                    import re
                    safe=code if isinstance(code,str) and (code in allowed or re.fullmatch(r'fomo_http_[0-9]{3}',code)) else 'unknown_bridge_error'
                    structure=result.get('structure')
                    detail={}
                    if isinstance(structure,dict):
                        for field in ('address_present','address_matches','network_present','network_matches'):
                            detail[field]=structure.get(field) is True
                        for field in ('row_keys','nested_token_keys','root_keys','body_keys','body_field_types'):
                            values=structure.get(field)
                            if isinstance(values,list):
                                detail[field]=[v for v in values[:30] if isinstance(v,str) and re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,39}(?::(?:array|null|object|string|number|boolean))?',v)]
                    raise RuntimeError('FOMO market request failed: '+safe+(' '+json.dumps(detail) if detail else ''))
                rows=result.get('rows')
                if not isinstance(rows,list) or len(rows)>20:raise ValueError('invalid FOMO batch response')
                stamp=time.time();seen=set()
                for row in rows:
                    token=row.get('address') if isinstance(row,dict) else None
                    if token not in batch or token in seen:raise ValueError('unexpected or duplicate FOMO mint')
                    seen.add(token);output[token]=parse_metrics(token,row,stamp)
            return output
        finally:
            with self.lock:
                ids={item[0] for item in items}
                for job_id in ids:self.pending.pop(job_id,None)
                self.jobs=[j for j in self.jobs if j['id'] not in ids]


def collect_batches(bridge,tokens):
    """Keep successful batches and give each browser request its own deadline."""
    tokens=list(dict.fromkeys(tokens));metrics={};errors=[]
    for offset in range(0,len(tokens),20):
        batch=tokens[offset:offset+20]
        try:metrics.update(bridge.tokens(batch))
        except (RuntimeError,ValueError,TypeError,KeyError) as error:
            # Only bridge-generated messages have already been sanitized.
            message=str(error)
            safe=message if isinstance(error,RuntimeError) and message.startswith(('FOMO browser bridge timed out','FOMO market request failed:')) else type(error).__name__
            errors.append(dict(batch=offset//20,requested=len(batch),reason=safe))
    return metrics,errors


def status():
    config=setup()
    try:
        request=Request('http://127.0.0.1:8766/status',headers={'Authorization':'Bearer '+config['key']})
        with urlopen(request,timeout=2) as response:return json.loads(response.read(4096))
    except OSError:return dict(connected=False,session_ready=False,bridge_running=False)


def enrich(snapshot,metrics):
    if metrics.get('token')!=snapshot.get('token'):raise ValueError('FOMO enrichment mint mismatch')
    result=dict(snapshot)
    # Keep pair price/liquidity and creation time for quote safety. Token-level
    # venue measurements serve the post's free cut and are separately attributed.
    result['fomo']=metrics
    return result

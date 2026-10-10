"""Bounded, local-only WorldMonitor context; no fabricated fallback."""
import json
import math
import time
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import Request,build_opener
from .http import NoRedirect

CONTEXT=(
 '/api/intelligence/v1/get-social-velocity',
 '/api/intelligence/v1/list-cross-source-signals',
 '/api/market/v1/list-crypto-quotes',
 '/api/market/v1/get-fear-greed-index')

class WorldMonitor:
    def __init__(self,url='http://127.0.0.1:3000'):
        p=urlsplit(url)
        if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost') or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:
            raise ValueError('WorldMonitor must be an HTTP loopback origin')
        self.url=url.rstrip('/');self.opener=build_opener(NoRedirect())
    def get(self,path):
        with self.opener.open(Request(self.url+path,headers={'Accept':'application/json'}),timeout=5) as r:
            raw=r.read(262145)
        if len(raw)>262144:raise ValueError('WorldMonitor response too large')
        data=json.loads(raw)
        if not isinstance(data,dict) or data.get('error'):raise ValueError('WorldMonitor returned invalid data')
        return data
    def context(self):
        results=[]
        for path in CONTEXT:
            try:
                data=self.get(path)
                projected,usable,reason,freshness_known=project(path,data,time.time())
                results.append(dict(endpoint=path,available=usable,data=projected if usable else None,reason=reason,freshness_known=freshness_known))
            except Exception as e:results.append(dict(endpoint=path,available=False,data=None,error_type=type(e).__name__))
        return dict(degraded=any(not r['available'] or not r.get('freshness_known',False) for r in results),endpoints=results)
    def health(self):
        result=self.context()
        try:
            data=self.get('/api/prediction/v1/list-prediction-markets?page_size=1')
            available=data.get('dataAvailable') is True and fresh(data.get('fetchedAt'),time.time(),3600)
            result['prediction']=dict(available=available)
        except Exception as e:result['prediction']=dict(available=False,error_type=type(e).__name__)
        result['ready']=not result['degraded'] and result['prediction']['available']
        return result
    def ready_with_retries(self,attempts=10,delay=3,sleep=time.sleep):
        result=None
        for attempt in range(attempts):
            result=self.health()
            if result['ready']:break
            if attempt+1<attempts:sleep(delay)
        return dict(result,attempts=attempt+1)


def fresh(value,now,max_age):
    if isinstance(value,str):
        try:value=float(value)
        except ValueError:
            try:value=datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
            except ValueError:return False
    if type(value) not in (int,float) or not math.isfinite(value) or value<=0:return False
    if value>1e11:value/=1000
    return 0<=now-value<=max_age


def project(path,data,now):
    if data.get('unavailable') is True or data.get('provider')=='degraded' or data.get('dataAvailable') is False:
        return None,False,'provider_unavailable',False
    if path.endswith('get-social-velocity'):
        key,fields,stamp,age='posts',('id','title','subreddit','url','velocityScore'),data.get('fetchedAt'),1800
    elif path.endswith('list-cross-source-signals'):
        key,fields,stamp,age='signals',('id','summary','theater','contributingTypes','signalCount'),data.get('evaluatedAt'),1800
    elif path.endswith('list-crypto-quotes'):
        key,fields,stamp,age='quotes',('symbol','price','change'),data.get('fetchedAt'),300
    else:
        stamp=data.get('seededAt')
        score=data.get('compositeScore')
        valid=type(score) in (int,float) and math.isfinite(score) and 0<=score<=100 and bool(data.get('compositeLabel')) and fresh(stamp,now,86400)
        return {k:data.get(k) for k in ('compositeScore','compositeLabel','seededAt')},valid,None if valid else 'missing_or_stale_context',valid
    rows=data.get(key)
    known=fresh(stamp,now,age)
    if not isinstance(rows,list) or (not rows and key!='signals'):return None,False,'missing_context',known
    # Current crypto schema lacks a source timestamp: label that limit explicitly.
    if not known and not (key=='quotes' and stamp is None):return None,False,'missing_or_stale_context',False
    output={key:[], 'source_timestamp':stamp, 'truncated':False}
    for row in rows:
        if not isinstance(row,dict):continue
        entry={k:(row[k][:240] if isinstance(row.get(k),str) else row.get(k)) for k in fields}
        trial=dict(output,**{key:output[key]+[entry]})
        if len(json.dumps(trial,separators=(',',':'),allow_nan=False).encode())>1000:
            output['truncated']=True;break
        output[key].append(entry)
    valid=bool(output[key]) or key=='signals' and not rows
    return output,valid,None if known else 'source_timestamp_unavailable',known

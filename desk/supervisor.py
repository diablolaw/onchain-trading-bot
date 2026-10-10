"""Local paper workers with bounded restart supervision; no trade credentials."""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def run(db='data/post-paper.sqlite',decision_interval=900,worldmonitor_url=None,sizing_file=None,observe_only=False,social_port=None):
    if decision_interval<60:raise ValueError('decision interval must be >=60')
    root=Path(__file__).resolve().parent.parent
    db=str(Path(db).resolve())
    if not observe_only:
        validate_entry_setup(root,db,sizing_file)
    common=[sys.executable,'-m','desk','--db',db,'--config',str(root/'post-paper.config.json')]
    decision=common+['--ai-config',str(root/'post-ai.config.json'),'ai-watch','--discovery-feed','--fomo','--limit','3','--interval',str(decision_interval)]
    if sizing_file:decision+=['--sizing-file',str(Path(sizing_file).resolve())]
    if observe_only:decision+=['--observe-only']
    if worldmonitor_url:decision+=['--worldmonitor-url',worldmonitor_url]
    commands={'SCAN':common+['discovery-watch','--interval','30'], 'VET_JEV':decision,'RISK':common+['risk','--interval','300'],'JUDGE':common+['judge-serve']}
    if social_port:
        # The Grok SOCIAL seat's local endpoint. The HTTPS tunnel in front of it is still run separately.
        commands['SOCIAL']=common+['judge-serve','--social-only','--port',str(social_port)]
    disabled=set();retry_at={};workers={};restarts={name:[] for name in commands};events=queue.Queue()
    last_output={}
    # JUDGE and SOCIAL are request-driven and may legitimately remain silent indefinitely.
    silence_limits={'SCAN':300,'RISK':600,'VET_JEV':decision_interval+900}
    def start(name):
        process=subprocess.Popen(commands[name],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        workers[name]=process
        last_output[name]=time.monotonic()
        def read():
            for line in process.stdout:
                if workers.get(name) is process:last_output[name]=time.monotonic()
                events.put(dict(seat=name,output=line.rstrip()))
            process.stdout.close()
        threading.Thread(target=read,daemon=True).start()
    try:
        for name in commands:start(name)
        heartbeat=0
        while True:
            try:print(json.dumps(events.get(timeout=1)),flush=True)
            except queue.Empty:pass
            now=time.monotonic()
            if now-heartbeat>=60:
                print(json.dumps(dict(supervisor='heartbeat',mode='paper_only',workers={n:p.poll() is None for n,p in workers.items()})),flush=True);heartbeat=now
            for name,process in list(workers.items()):
                if name in disabled:continue
                if process.poll() is None and name in silence_limits and now-last_output[name]>silence_limits[name]:
                    print(json.dumps(dict(supervisor='worker_timeout',seat=name,silent_seconds=int(now-last_output[name]))),flush=True)
                    # Reap the old worker before a replacement can use the same DB.
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                if process.poll() is None or now<retry_at.get(name,0):continue
                restarts[name]=[t for t in restarts[name] if now-t<600]
                if len(restarts[name])>=2:
                    print(json.dumps(dict(supervisor='entries_halted',seat=name,reason='repeated_worker_failure',risk_preserved=True)),flush=True)
                    for entry_seat in ('SCAN','VET_JEV','JUDGE'):
                        disabled.add(entry_seat)
                        p=workers[entry_seat]
                        if p.poll() is None:p.terminate()
                    if name=='RISK':
                        # Continue trying to restore exits, with bounded retry frequency.
                        retry_at[name]=now+30
                        restarts[name]=[]
                    continue
                restarts[name].append(now);print(json.dumps(dict(supervisor='restart',seat=name,exit_code=process.returncode)),flush=True);start(name)
    except KeyboardInterrupt:return dict(stopped=True,reason='operator_interrupt')
    finally:
        for p in workers.values():
            if p.poll() is None:p.terminate()
        for p in workers.values():
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()


def validate_entry_setup(root,db,sizing_file):
    """Reject impossible entry configuration before starting a billed worker."""
    import sqlite3
    from .core import Rules
    from .post_policy import size
    r=Rules(**json.loads((root/'post-paper.config.json').read_text()))
    cash=r.initial_cash
    if Path(db).exists():
        with sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True) as connection:
            cash=float(connection.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])
    if not sizing_file:
        if not r.paper_fixed_fraction:raise ValueError('post-run requires --sizing-file PATH, a paper_fixed_fraction in post-paper.config.json, or explicit --observe-only')
        from .post_policy import fixed_size
        # Pools at least ten times the minimum liquidity must be sizeable after the partial cut.
        result=fixed_size(cash,cash,r.paper_fixed_fraction,r.min_liquidity*10,partial=True,liquidity_fraction=r.max_ticket_liquidity_pct/100)
        if not 0<result['dollars']<=r.ticket:raise ValueError('paper_fixed_fraction is not viable for current cash, ticket cap, liquidity cap and fee ceiling; use --observe-only or correct the configuration')
        return
    data=json.loads(Path(sizing_file).read_text())
    if not isinstance(data,dict) or not data:raise ValueError('sizing file must contain sourced per-token forecasts')
    valid=False
    for e in data.values():
        if not isinstance(e,dict) or not isinstance(e.get('source'),str) or not e['source']:continue
        stamp=e.get('observed_at')
        if type(stamp) not in (int,float) or not 0<=time.time()-stamp<=3600:continue
        result=size(cash,cash,e.get('probability'),e.get('payoff'),r.min_liquidity*10,partial=True,
            locked_cash=e.get('locked_cash',0),operating_reserve=e.get('operating_reserve',0),max_fee_pct=e.get('max_fee_pct',3),liquidity_fraction=r.max_ticket_liquidity_pct/100)
        valid |= 0<result['dollars']<=r.ticket
    if not valid:raise ValueError('no fresh sizing input is viable for current paper cash, partial sizing, ticket cap and fee ceiling; use --observe-only or correct the inputs/configuration')

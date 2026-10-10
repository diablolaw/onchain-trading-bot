"""CHIEF's Telegram reports: every cycle, end of day, and >10% equity moves. Paper results only."""
import json
import os
import time
from datetime import datetime, timezone

from .http import JsonHTTP

SCHEMA='CREATE TABLE IF NOT EXISTS report_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)'


class Telegram:
    def __init__(self,http=None):
        self.token=os.environ.get('TELEGRAM_BOT_TOKEN');self.chat=os.environ.get('TELEGRAM_CHAT_ID')
        self.http=http or JsonHTTP(attempts=2,timeout=10,max_bytes=65536)

    @property
    def configured(self):return bool(self.token and self.chat)

    def send(self,text):
        if not self.configured:return False
        reply,_=self.http.request('https://api.telegram.org/bot'+self.token+'/sendMessage',payload=dict(chat_id=self.chat,text=text[:4000],disable_web_page_preview=True))
        if not isinstance(reply,dict) or reply.get('ok') is not True:raise RuntimeError('Telegram rejected the message')
        return True


def state(desk,key,value=None):
    desk.db.execute(SCHEMA)
    if value is None:
        row=desk.db.execute('SELECT value FROM report_state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    with desk.db:desk.db.execute('INSERT OR REPLACE INTO report_state VALUES (?,?)',(key,json.dumps(value)))


def cycle_text(result):
    d=result.get('discovery') or {};cut=d.get('free_cut') or {}
    line='PAPER cycle '+str(result.get('state'))+': '+str(cut.get('observed',0))+' seen, '+str(cut.get('passed',0))+' passed free cut, '+str(result.get('shortlisted',0))+' shortlisted, '+str(result.get('calls',0))+' judge calls'
    return line+('. Paper buy: '+result['selected_token'] if result.get('paper_buys') else '. No trade.')


def day_text(desk,day):
    start=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp();end=start+86400
    count=lambda sql:desk.db.execute(sql,(start,end)).fetchone()[0]
    cycles=count('SELECT COUNT(*) FROM ai_cycles WHERE started_at>=? AND started_at<?')
    rejects=count("SELECT COUNT(*) FROM ai_decisions WHERE at>=? AND at<? AND stage IN ('deterministic_reject','post_soft_reject')")
    buys=count("SELECT COUNT(*) FROM fills WHERE side='buy' AND at>=? AND at<?")
    closes=count("SELECT COUNT(*) FROM positions WHERE status='closed' AND closed_at>=? AND closed_at<?")
    pnl=desk.db.execute("SELECT COALESCE(SUM(CAST(pnl AS REAL)),0) FROM positions WHERE status='closed' AND closed_at>=? AND closed_at<?",(start,end)).fetchone()[0]
    equity=float(desk.report()['marked_equity_usd'])
    return f'PAPER end of day {day}: {cycles} cycles, {rejects} rejections, {buys} fills, {closes} closes, realized P&L ${pnl:.2f} ({pnl/equity*100 if equity else 0:.2f}% of working capital)'


def after_cycle(desk,result,telegram=None,now=None):
    """Send what the post's CHIEF sends. Unconfigured Telegram records 'not_configured'; failures never stop the desk."""
    telegram=telegram or Telegram();now=time.time() if now is None else now
    if not telegram.configured:return 'not_configured'
    messages=[cycle_text(result)]
    today=datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d')
    last_day=state(desk,'last_day')
    if last_day and last_day!=today:messages.append(day_text(desk,last_day))
    equity=float(desk.report(now)['marked_equity_usd'])
    reference=state(desk,'equity_reference')
    if reference and abs(equity/reference-1)>.10:
        messages.append(f'PAPER equity moved {(equity/reference-1)*100:+.1f}% to ${equity:.2f}')
        reference=None
    try:
        for message in messages:telegram.send(message)
    except Exception as error:return 'failed:'+type(error).__name__
    state(desk,'last_day',today)
    if not reference:state(desk,'equity_reference',equity)
    return 'sent'

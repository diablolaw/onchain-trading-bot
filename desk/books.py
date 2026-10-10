"""Original BOOK branch: public market data and explicit independent forecasts.
No venue authentication or order submission. Never infer forecasts from prices.
"""
import json
import time
from .http import JsonHTTP
from .core import number


def collect(http=None):
    http=http or JsonHTTP(attempts=1,max_bytes=2*1024*1024)
    markets=[];errors=[]
    endpoints=(('polymarket','https://gamma-api.polymarket.com/markets?closed=false&order=volumeNum&limit=50'),('kalshi','https://external-api.kalshi.com/trade-api/v2/markets?limit=100&status=open'))
    for venue,url in endpoints:
        try:
            payload,stamp=http.request(url)
            rows=payload if venue=='polymarket' else payload.get('markets')
            if not isinstance(rows,list):raise ValueError('invalid market response')
            for row in rows[:100]:
                if not isinstance(row,dict):continue
                if venue=='polymarket':
                    outcomes=row.get('outcomes');prices=row.get('outcomePrices')
                    if isinstance(outcomes,str):outcomes=json.loads(outcomes)
                    if isinstance(prices,str):prices=json.loads(prices)
                    if not isinstance(outcomes,list) or not isinstance(prices,list) or len(outcomes)!=2 or outcomes!=['Yes','No']:continue
                    mid=number(prices[0]);bid=number(row.get('bestBid'));ask=number(row.get('bestAsk'))
                    market_id=str(row.get('id',''));title=row.get('question');closes=row.get('endDate');active=row.get('acceptingOrders') is True;liq=number(row.get('liquidityNum'))
                else:
                    mid=number(row.get('last_price_dollars'));bid=number(row.get('yes_bid_dollars'));ask=number(row.get('yes_ask_dollars'))
                    market_id=row.get('ticker');title=row.get('title');closes=row.get('close_time');active=row.get('status') in ('open','active');liq=number(row.get('liquidity_dollars'))
                if not market_id or not isinstance(title,str) or bid is None or ask is None or not 0<=bid<=ask<=1:continue
                markets.append(dict(venue=venue,market_id=market_id,question=title,closes_at=closes,bid=bid,ask=ask,market_prob=mid,spread=ask-bid,liquidity=liq,accepting_orders=active,observed_at=stamp))
        except Exception as e:errors.append(dict(venue=venue,error_type=type(e).__name__))
    return dict(markets=markets,errors=errors,degraded=bool(errors),market_only=True)


def evaluate(markets,forecasts,max_spread,max_ticket,now=None):
    now=time.time() if now is None else now
    results=[]
    for m in markets:
        key=m['venue']+':'+m['market_id'];f=forecasts.get(key)
        if not isinstance(f,dict):continue
        p=f.get('probability');stamp=f.get('observed_at')
        if type(p) not in (int,float) or not 0<=p<=1 or type(stamp) not in (int,float) or not 0<=now-stamp<=172800:continue
        # Bind exact ID, question and closing timestamp. Never fuzzy-match books.
        if f.get('question')!=m['question'] or f.get('closes_at')!=m['closes_at'] or not isinstance(f.get('source'),str) or not f['source']:continue
        edge=p-m['ask'];reasons=[]
        if edge<=.08:reasons.append('edge_not_above_8pct')
        if m['spread']>max_spread:reasons.append('spread')
        if not m['accepting_orders']:reasons.append('orders_closed')
        if m['liquidity'] is None or m['liquidity']<max_ticket*50:reasons.append('liquidity')
        results.append(dict(**m,my_prob=p,edge=edge,forecast_source=f['source'],forecast_age_hours=(now-stamp)/3600,action='STAKE_REQUEST' if not reasons else 'SKIP',reasons=reasons))
    return results

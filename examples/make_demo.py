"""Produce synthetic evidence and observations; these are not real market data."""
import json
from pathlib import Path

now = 1800000000
mint = 'DemoMint11111111111111111111111111111'
pair = 'DemoPool11111111111111111111111111111'
events = []
for delta, price, volume6 in [(0, 1, 10000), (15, 1.05, 10000), (30, 1.10, 100)]:
    events.append(dict(observed_at=now+delta,
                       pair=dict(chainId='solana', dexId='demo', pairAddress=pair,
                                 baseToken=dict(address=mint, symbol='DEMO'),
                                 priceUsd=str(price), liquidity=dict(usd=100000),
                                 volume=dict(h6=volume6, h24=40000), pairCreatedAt=(now-86400)*1000),
                       holders=dict(token=mint, observed_at=now+delta, source='synthetic demo',
                                    method='owner_aggregated', top_owner_pct=5, top10_owner_pct=25)))
Path(__file__).with_name('demo.json').write_text(json.dumps(events, indent=2)+'\n')

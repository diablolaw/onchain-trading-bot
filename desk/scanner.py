"""Public DexScreener market data; discovery is a profile sample, not all Solana."""
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .core import normalize, number

ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


class DexScreener:
    base = 'https://api.dexscreener.com'

    def __init__(self, recorder, attempts=3, timeout=10):
        self.recorder = recorder
        self.attempts = attempts
        self.timeout = timeout
        self.last_request = 0

    def get(self, path):
        for attempt in range(self.attempts):
            # Conservative cap below the lowest documented endpoint limit.
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                request = Request(self.base + path, headers={'User-Agent': 'onchain-paper-desk/0.1'})
                with urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                stamp = time.time()
                self.recorder(path, data, stamp)
                return data, stamp
            except HTTPError as error:
                if error.code != 429 and error.code < 500:
                    raise
                if attempt + 1 == self.attempts:
                    raise
                time.sleep(min(10, 2 ** attempt))
            except (URLError, TimeoutError):
                if attempt + 1 == self.attempts:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError('request attempts exhausted')

    def discover(self, limit=20):
        profiles, _ = self.get('/token-profiles/latest/v1')
        if not isinstance(profiles, list):
            raise ValueError('unexpected token profile response')
        return sorted({p['tokenAddress'] for p in profiles
                       if isinstance(p, dict) and p.get('chainId') == 'solana'
                       and ADDRESS.fullmatch(p.get('tokenAddress', ''))})[:limit]

    def pairs(self, token):
        if not ADDRESS.fullmatch(token):
            raise ValueError('invalid Solana mint address')
        data, observed_at = self.get('/token-pairs/v1/solana/' + token)
        if not isinstance(data, list):
            raise ValueError('unexpected token pair response')
        return [normalize(p, observed_at) for p in data if isinstance(p, dict)
                and p.get('chainId') == 'solana' and (p.get('baseToken') or {}).get('address') == token
                and p.get('pairAddress')]

    def batch_pairs(self, tokens):
        """Up to 30 mints per documented tokens request; validate every identity."""
        from .onchain import address
        tokens=list(dict.fromkeys(address(t) for t in tokens))
        found={token:[] for token in tokens}
        for offset in range(0,len(tokens),30):
            batch=tokens[offset:offset+30]
            payload,stamp=self.get('/tokens/v1/solana/'+','.join(batch))
            if not isinstance(payload,list): raise ValueError('invalid batch market response')
            for p in payload:
                if not isinstance(p,dict): continue
                token=(p.get('baseToken') or {}).get('address')
                if p.get('chainId')!='solana' or token not in batch: continue
                try: address(p.get('pairAddress'))
                except ValueError: continue
                found[token].append(normalize(p,stamp))
        return {token:best_pair(pairs) for token,pairs in found.items()}

    def pair(self, pair, token):
        if not ADDRESS.fullmatch(pair):
            raise ValueError('invalid Solana pair address')
        data, observed_at = self.get('/latest/dex/pairs/solana/' + pair)
        candidates = data.get('pairs') if isinstance(data, dict) else None
        if not isinstance(candidates, list):
            raise ValueError('missing pair response')
        for p in candidates:
            if (p.get('chainId') == 'solana' and p.get('pairAddress') == pair
                    and (p.get('baseToken') or {}).get('address') == token):
                return normalize(p, observed_at)
        raise ValueError('open position pair is absent from response')


def best_pair(pairs):
    return min(pairs, key=lambda p: (-(number(p.get('liquidity')) or 0), p['pair'])) if pairs else None

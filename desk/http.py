"""Bounded JSON transport. Credentials and endpoint URLs never enter audit logs."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None  # Do not forward authentication headers to another endpoint.


class JsonHTTP:
    def __init__(self, spacing=2.1, timeout=10, attempts=3, max_bytes=32*1024*1024):
        self.spacing, self.timeout, self.attempts = spacing, timeout, attempts
        self.max_bytes = max_bytes
        self.last = 0
        self.opener = build_opener(NoRedirect())

    def request(self, url, headers=None, payload=None):
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise ValueError('provider URL must use HTTPS without userinfo')
        for attempt in range(self.attempts):
            time.sleep(max(0, self.spacing-(time.monotonic()-self.last)))
            self.last = time.monotonic()
            try:
                body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
                request = Request(url, data=body, headers={'User-Agent':'onchain-paper-desk/0.2',
                                  'Content-Type':'application/json', **(headers or {})})
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(self.max_bytes+1)
                if len(raw) > self.max_bytes:
                    raise ValueError('provider response exceeds size limit')
                def invalid(value):
                    raise ValueError('nonfinite JSON number')
                return json.loads(raw, parse_constant=invalid), time.time()
            except HTTPError as error:
                retry = error.code == 429 or error.code >= 500
                error.close()
                if not retry or attempt+1 == self.attempts:
                    raise RuntimeError(f'provider HTTP {error.code}') from None
            except (URLError, TimeoutError, OSError):
                if attempt+1 == self.attempts:
                    raise RuntimeError('provider network request failed') from None
            time.sleep(min(4, 2**attempt))
        raise RuntimeError('provider request failed')

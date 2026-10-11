// Session storage stays in browser memory across worker suspension; never sent locally.
let authorization = null;
let busy = false;
let cookieSessionObserved = false;
let diagnostics = {requests_seen: 0, matching_origin: false, authorization_header_seen: false, bearer_header_seen: false, cookie_session_seen: false};
chrome.webRequest.onBeforeSendHeaders.addListener(details => {
  diagnostics.requests_seen += 1;
  if (!['https://fomo.family','https://www.fomo.family'].includes(details.initiator)) return;
  diagnostics.matching_origin = true;
  const header = (details.requestHeaders || []).find(h => h.name.toLowerCase() === 'authorization');
  const cookieHeader = (details.requestHeaders || []).find(h => h.name.toLowerCase() === 'cookie');
  // Inspect presence only. Chrome owns and attaches cookie values; never retain them.
  cookieSessionObserved = !!(cookieHeader && /(?:^|;\s*)privy-token=[^;\s]+/.test(cookieHeader.value || ''));
  diagnostics.cookie_session_seen = cookieSessionObserved;
  chrome.storage.session.set({cookieSessionObserved});
  diagnostics.authorization_header_seen ||= !!header;
  diagnostics.bearer_header_seen ||= !!(header && /^Bearer [^\s]+$/.test(header.value || ''));
  chrome.storage.session.set({fomoDiagnostics: diagnostics});
  if (header && /^Bearer [^\s]+$/.test(header.value || '')) {
    authorization = header.value;
    chrome.storage.session.set({fomoAuthorization: authorization});
  }
}, {urls: ['https://prod-api.fomo.family/*']}, ['requestHeaders', 'extraHeaders']);

// FOMO's own session token, read from its cookie so requests can carry it alone.
// Sending only this header avoids attaching every fomo.family cookie, which caused HTTP 431.
async function cookieBearer() {
  // getAll by domain also finds the cookie when it is set on a subdomain or .fomo.family.
  const cookies = await chrome.cookies.getAll({domain: 'fomo.family', name: 'privy-token'}).catch(() => []);
  diagnostics.cookie_token_found = cookies.some(c => /^[A-Za-z0-9._-]{20,4096}$/.test(c.value || ''));
  const cookie = cookies.find(c => /^[A-Za-z0-9._-]{20,4096}$/.test(c.value || ''));
  return cookie ? 'Bearer ' + cookie.value : null;
}
// FOMO's login token is short-lived and only renewed while a FOMO page loads or runs.
// When it has lapsed, reload the FOMO tab (or open one in the background) instead of asking the user to.
const FOMO_TABS = ['https://fomo.family/*', 'https://www.fomo.family/*'];
async function renewFomoSession() {
  const {lastRenewal = 0} = await chrome.storage.session.get('lastRenewal');
  if (Date.now() - lastRenewal < 5 * 60 * 1000) return;
  await chrome.storage.session.set({lastRenewal: Date.now()});
  diagnostics.session_renewals = (diagnostics.session_renewals || 0) + 1;
  const tabs = await chrome.tabs.query({url: FOMO_TABS}).catch(() => []);
  if (tabs.length) await chrome.tabs.reload(tabs[0].id).catch(() => null);
  else await chrome.tabs.create({url: 'https://fomo.family/', active: false}).catch(() => null);
}
// Seconds until a JWT bearer expires, or null when it carries no readable expiry.
function secondsLeft(bearer) {
  try {
    const part = bearer.split(' ')[1].split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const exp = JSON.parse(atob(part.padEnd(part.length + (4 - part.length % 4) % 4, '='))).exp;
    return typeof exp === 'number' ? exp - Date.now() / 1000 : null;
  } catch (_) { return null; }
}
async function filterTokens(tokens, bearer) {
  return fetch('https://prod-api.fomo.family/proxy/filterTokens', {
    // A bearer request omits cookies; only the cookie fallback lets Chrome attach them.
    method: 'POST', credentials: bearer ? 'omit' : 'include',
    headers: {'Content-Type': 'application/json', ...(bearer ? {'Authorization': bearer} : {})},
    body: JSON.stringify(tokens.map(t => t + ':1399811149')),
    redirect: 'error', signal: AbortSignal.timeout(20000)
  });
}

// The FOMO page reports its live login token (see fomo-token.js). This is the primary session source:
// it stays current while the page is open, whether or not the page itself calls the API.
chrome.runtime.onMessage.addListener((message, sender) => {
  if (!['https://fomo.family', 'https://www.fomo.family'].includes(sender.origin)) return;
  if (message?.type !== 'fomo-page-token' || typeof message.token !== 'string') return;
  if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(message.token) || message.token.length > 4096) return;
  const bearer = 'Bearer ' + message.token;
  const left = secondsLeft(bearer);
  if (left === null || left <= 0) return;
  authorization = bearer;
  diagnostics.page_token_seen = true;
  chrome.storage.session.set({fomoAuthorization: authorization, fomoDiagnostics: diagnostics});
  poll();
});

async function local(path, key, data) {
  const response = await fetch('http://127.0.0.1:8766' + path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
    ...(data === undefined ? {} : {body: JSON.stringify(data)}),
    redirect: 'error', signal: AbortSignal.timeout(5000)
  });
  if (!response.ok) throw new Error('bridge_unavailable');
  return response.json();
}
function rowsFrom(payload, tokens) {
  // FOMO wraps market rows in responseObject; also accept the prior data envelope.
  if (payload?.success === false) throw new Error('fomo_api_rejected');
  const body = payload?.responseObject ?? payload?.data ?? payload;
  const rows = Array.isArray(body) ? body : body && typeof body === 'object' ?
    Object.entries(body).map(([id, row]) => ({...row, _id: id})) : null;
  if (!rows || rows.length > 20) throw new Error('unsupported_response_shape');
  return rows.map(row => {
    const id = row._id?.split(':');
    const address = row.token?.address || row.address || row.tokenAddress || row.contractAddress || (id?.length === 2 ? id[0] : null);
    const networkId = row.token?.networkId ?? row.networkId ?? row.netId ?? (id?.length === 2 ? id[1] : null);
    if (!tokens.includes(address) || String(networkId) !== '1399811149') {
      const error = new Error('response_identity_mismatch');
      error.structure = {
        root_keys: payload && typeof payload === 'object' ? Object.keys(payload).filter(k => /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/.test(k)).slice(0,20) : [],
        body_keys: body && typeof body === 'object' ? Object.keys(body).filter(k => /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/.test(k)).slice(0,20) : [],
        body_field_types: body && !Array.isArray(body) && typeof body === 'object' ? Object.entries(body).slice(0,20).filter(([k]) => /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/.test(k)).map(([k,v]) => k + ':' + (Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v)) : [],
        row_keys: Object.keys(row).filter(k => /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/.test(k)).slice(0,30),
        address_present: typeof address === 'string',
        address_matches: tokens.includes(address),
        network_present: networkId !== undefined && networkId !== null,
        network_matches: String(networkId) === '1399811149',
        nested_token_keys: row.token && typeof row.token === 'object' ? Object.keys(row.token).filter(k => /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/.test(k)).slice(0,20) : []
      };
      throw error;
    }
    // Explicit allowlist: never forward headers, cookies, profile/wallet info or raw responses.
    return {address, networkId, marketCap: row.marketCap ?? row.mcap,
      liquidity: row.liquidity ?? row.liq, volume24: row.volume24 ?? row.volume24h ?? row.vol24,
      holders: row.holders ?? row.holderCount, priceUSD: row.priceUSD ?? row.priceUsd ?? row.price,
      createdAt: row.createdAt ?? row.created};
  });
}
async function poll() {
  if (busy) return;
  busy = true;
  try {
    const cookieState = await chrome.storage.session.get('cookieSessionObserved');
    cookieSessionObserved = cookieState.cookieSessionObserved === true;
    const savedDiagnostics = await chrome.storage.session.get('fomoDiagnostics');
    if (!diagnostics.requests_seen && savedDiagnostics.fomoDiagnostics) diagnostics = savedDiagnostics.fomoDiagnostics;
    if (!authorization) {
      const session = await chrome.storage.session.get('fomoAuthorization');
      authorization = session.fomoAuthorization || null;
    }
    // Renew ahead of expiry so a desk cycle never lands on a lapsed token.
    const left = authorization ? secondsLeft(authorization) : null;
    diagnostics.token_seconds_left = left === null ? null : Math.round(left);
    if (!authorization) await cookieBearer();
    if (left !== null && left < 600) await renewFomoSession();
    const {bridgeKey} = await chrome.storage.local.get('bridgeKey');
    if (!/^[a-f0-9]{64}$/.test(bridgeKey || '')) return;
    await local('/heartbeat', bridgeKey, {session_ready: !!authorization || cookieSessionObserved || !!(await cookieBearer()), diagnostics});
    const {jobs} = await local('/jobs', bridgeKey);
    for (const job of jobs) {
      let result = {id: job.id};
      try {
        const fromCookie = authorization ? null : await cookieBearer();
        if (!authorization && !fromCookie) await renewFomoSession();
        if (!authorization && !fromCookie && !cookieSessionObserved) throw new Error('session_not_observed_reload_fomo');
        if (!Array.isArray(job.tokens) || job.tokens.length > 20 || job.tokens.some(t => !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(t))) throw new Error('invalid_job');
        let response = await filterTokens(job.tokens, authorization || fromCookie);
        // If the API will not take the cookie token as a bearer, fall back to Chrome's cookies.
        if (fromCookie && (response.status === 401 || response.status === 403)) response = await filterTokens(job.tokens, null);
        if (response.status === 401 || response.status === 403) {authorization = null; cookieSessionObserved = false; await chrome.storage.session.remove(['fomoAuthorization','cookieSessionObserved']); await renewFomoSession(); throw new Error('session_expired_reload_fomo');}
        // 431: request headers too large. Only the cookie fallback sends the full cookie jar.
        // 'cookie_token_missing' means the extension could not read FOMO's login token (reload the extension, then FOMO).
        if (response.status === 431) throw new Error(authorization || fromCookie ? 'fomo_http_431' : 'fomo_headers_too_large_cookie_token_missing');
        if (!response.ok) throw new Error('fomo_http_' + response.status);
        const raw = await response.text();
        if (raw.length > 262144) throw new Error('response_too_large');
        result.rows = rowsFrom(JSON.parse(raw), job.tokens);
      } catch (e) {
        const allowed = ['session_not_observed_reload_fomo','session_expired_reload_fomo','fomo_headers_too_large_reload_fomo','fomo_headers_too_large_cookie_token_missing','invalid_job','unsupported_response_shape','response_identity_mismatch','response_too_large','fomo_api_rejected'];
        if (e.structure) result.structure = e.structure;
        result.error = allowed.includes(e.message) || /^fomo_http_\d{3}$/.test(e.message) ? e.message : 'market_request_failed';
      }
      await local('/result', bridgeKey, result);
    }
  } catch (_) { /* No credential-bearing exceptions or raw data enter logs. */ }
  finally {busy = false;}
}
chrome.alarms.create('market-bridge', {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(poll);
chrome.webRequest.onCompleted.addListener(poll, {urls: ['https://prod-api.fomo.family/*']});
chrome.storage.onChanged.addListener(poll);
poll();

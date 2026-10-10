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
    const {bridgeKey} = await chrome.storage.local.get('bridgeKey');
    if (!/^[a-f0-9]{64}$/.test(bridgeKey || '')) return;
    await local('/heartbeat', bridgeKey, {session_ready: !!authorization || cookieSessionObserved, diagnostics});
    const {jobs} = await local('/jobs', bridgeKey);
    for (const job of jobs) {
      let result = {id: job.id};
      try {
        if (!authorization && !cookieSessionObserved) throw new Error('session_not_observed_reload_fomo');
        if (!Array.isArray(job.tokens) || job.tokens.length > 20 || job.tokens.some(t => !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(t))) throw new Error('invalid_job');
        const response = await fetch('https://prod-api.fomo.family/proxy/filterTokens', {
          // Bearer sessions do not need the additional browser Cookie header.
          // Cookie-only sessions retain Chrome-managed cookies; never copy them.
          method: 'POST', credentials: authorization ? 'omit' : 'include',
          headers: {'Content-Type': 'application/json', ...(authorization ? {'Authorization': authorization} : {})},
          body: JSON.stringify(job.tokens.map(t => t + ':1399811149')),
          redirect: 'error', signal: AbortSignal.timeout(20000)
        });
        if (response.status === 401 || response.status === 403) {authorization = null; cookieSessionObserved = false; await chrome.storage.session.remove(['fomoAuthorization','cookieSessionObserved']); throw new Error('session_expired_reload_fomo');}
        // 431: the request's headers (in cookie-only mode, Chrome's fomo.family cookies) are too large.
        // Reloading FOMO captures a bearer session, which omits cookies on these requests.
        if (response.status === 431) throw new Error(authorization ? 'fomo_http_431' : 'fomo_headers_too_large_reload_fomo');
        if (!response.ok) throw new Error('fomo_http_' + response.status);
        const raw = await response.text();
        if (raw.length > 262144) throw new Error('response_too_large');
        result.rows = rowsFrom(JSON.parse(raw), job.tokens);
      } catch (e) {
        const allowed = ['session_not_observed_reload_fomo','session_expired_reload_fomo','fomo_headers_too_large_reload_fomo','invalid_job','unsupported_response_shape','response_identity_mismatch','response_too_large','fomo_api_rejected'];
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

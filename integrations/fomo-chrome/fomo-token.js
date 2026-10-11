// Runs inside fomo.family. FOMO's login library keeps its current access token in this page's
// localStorage and renews it there while the page is open, even when no API call is made.
// Forward that token to the extension so the bridge always has the page's live login.
// Only a JWT that has not expired is sent; it never leaves the extension.
function jwtExpiry(value) {
  try {
    const part = value.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const exp = JSON.parse(atob(part.padEnd(part.length + (4 - part.length % 4) % 4, '='))).exp;
    return typeof exp === 'number' ? exp : null;
  } catch (_) { return null; }
}
function currentToken() {
  let best = null, bestExp = 0;
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i) || '';
    if (!/token/i.test(key) || /refresh|identity|id_token/i.test(key)) continue;
    let value = localStorage.getItem(key) || '';
    try { const parsed = JSON.parse(value); if (typeof parsed === 'string') value = parsed; } catch (_) {}
    if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value) || value.length > 4096) continue;
    const exp = jwtExpiry(value);
    if (exp && exp * 1000 > Date.now() && exp > bestExp) { best = value; bestExp = exp; }
  }
  return best;
}
function report() {
  const token = currentToken();
  if (token) chrome.runtime.sendMessage({type: 'fomo-page-token', token}).catch(() => null);
}
report();
setInterval(report, 30000);
window.addEventListener('storage', report);

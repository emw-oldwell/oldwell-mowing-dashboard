// Request authentication for every write endpoint.
//
// Until now these endpoints were completely open: no auth, and
// Access-Control-Allow-Origin: *. Anyone who knew the URL could upload photos,
// modify the schedule, or delete jobs by ID — and job IDs are sequential and were
// published in data.json. Each write also commits to the repo using a PAT with
// contents:write, so an open write endpoint was an open door to the repository.
//
// This is containment, not the final answer. A shared key shipped to the browser
// is visible to anyone who loads the app, so it stops opportunistic and scripted
// abuse but not a determined reader of the page source. The real fix is
// per-contractor tokens plus an office session; this exists so the window between
// "found the hole" and "shipped the fix" is not spent wide open.

const APP_KEY = process.env.APP_KEY;

/**
 * Allowed browser origins. Defaults cover the production alias and the app's own
 * pages; add previews via ALLOWED_ORIGINS (comma-separated) rather than widening
 * back to "*".
 */
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const DEFAULT_ORIGINS = [
  'https://oldwell-mowing-api.vercel.app',
  'https://oldwell-mowing-dashboard.vercel.app',
];

function allowedOrigins() {
  return ALLOWED_ORIGINS.length ? ALLOWED_ORIGINS : DEFAULT_ORIGINS;
}

/**
 * Reflect the request's origin only when it is on the allow-list. Never "*" —
 * a wildcard here let any page on the internet drive these endpoints from a
 * visitor's browser.
 */
function setCors(req, res) {
  const origin = req.headers?.origin;
  if (origin && allowedOrigins().includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
  }
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-App-Key');
  res.setHeader('Access-Control-Max-Age', '86400');
}

/** Constant-time compare so a wrong key cannot be recovered by timing. */
function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * Returns true when the request may proceed. Sends the error response itself
 * and returns false otherwise, so handlers can `if (!requireAuth(req, res)) return;`.
 *
 * Fails closed: an unset APP_KEY rejects every write rather than allowing them.
 * An unconfigured deployment should refuse to act, not act without checking.
 */
function requireAuth(req, res) {
  if (!APP_KEY) {
    res.status(503).json({ error: 'APP_KEY is not configured; write endpoints are disabled.' });
    return false;
  }

  const provided = req.headers?.['x-app-key'];
  if (!safeEqual(provided, APP_KEY)) {
    res.status(401).json({ error: 'Unauthorized' });
    return false;
  }

  return true;
}

module.exports = { setCors, requireAuth, allowedOrigins };

// GET /api/health — quick pre-deploy sanity check. No-auth, no GitHub call.
// Returns 200 if the function runs at all and reports whether GITHUB_TOKEN
// is configured. Use this to confirm the Vercel deploy is alive before
// pointing the app at it.

const { setCors } = require('./_github');

module.exports = function handler(req, res) {
  setCors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  return res.status(200).json({
    ok: true,
    service: 'oldwell-mowing-api',
    tokenConfigured: Boolean(process.env.GITHUB_TOKEN),
    repo: process.env.GITHUB_REPO || 'emw-oldwell/oldwell-mowing-dashboard',
    branch: process.env.GITHUB_BRANCH || 'main',
    nodeVersion: process.version,
    now: new Date().toISOString(),
  });
};

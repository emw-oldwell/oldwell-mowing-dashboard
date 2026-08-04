// GET /api/health — liveness only.
//
// This used to return the repo name, branch, node version and whether a GitHub
// token was configured, to anyone who asked. A health check should confirm the
// service is up, not hand an unauthenticated caller a description of what it is
// wired to and what credentials it holds.

const { setCors } = require('./_auth');

module.exports = function handler(req, res) {
  setCors(req, res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  return res.status(200).json({ ok: true, service: 'oldwell-mowing-api' });
};

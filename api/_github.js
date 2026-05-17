// Shared helpers for committing files to this repo via the GitHub Contents API.
// Uses GITHUB_TOKEN from env (a PAT with contents:write on this repo).

const REPO = process.env.GITHUB_REPO || 'emw-oldwell/oldwell-mowing-dashboard';
const BRANCH = process.env.GITHUB_BRANCH || 'main';
const TOKEN = process.env.GITHUB_TOKEN;

function ghHeaders() {
  return {
    Authorization: `Bearer ${TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'oldwell-mowing-app',
  };
}

async function getFile(path) {
  const url = `https://api.github.com/repos/${REPO}/contents/${encodeURIComponent(path)}?ref=${BRANCH}`;
  const r = await fetch(url, { headers: ghHeaders() });
  if (r.status === 404) return { sha: null, content: null };
  if (!r.ok) throw new Error(`GET ${path}: ${r.status} ${await r.text()}`);
  const j = await r.json();
  return { sha: j.sha, content: Buffer.from(j.content, 'base64').toString('utf8') };
}

async function putFile(path, contentBuffer, message, sha) {
  const url = `https://api.github.com/repos/${REPO}/contents/${path.split('/').map(encodeURIComponent).join('/')}`;
  const body = {
    message,
    content: contentBuffer.toString('base64'),
    branch: BRANCH,
    committer: { name: 'mowing-app-bot', email: 'app-bot@oldwellco.com' },
    author: { name: 'mowing-app-bot', email: 'app-bot@oldwellco.com' },
  };
  if (sha) body.sha = sha;
  const r = await fetch(url, { method: 'PUT', headers: { ...ghHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) {
    const err = new Error(`PUT ${path}: ${r.status} ${await r.text()}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

// Best-effort delete. Returns {deleted: true} on success, {skipped: 'not found'} for 404.
async function deleteFile(path, message) {
  const encoded = path.split('/').map(encodeURIComponent).join('/');
  const head = await fetch(`https://api.github.com/repos/${REPO}/contents/${encoded}?ref=${BRANCH}`, { headers: ghHeaders() });
  if (head.status === 404) return { skipped: 'not found' };
  if (!head.ok) throw new Error(`GET ${path}: ${head.status}`);
  const { sha } = await head.json();
  const r = await fetch(`https://api.github.com/repos/${REPO}/contents/${encoded}`, {
    method: 'DELETE',
    headers: { ...ghHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      branch: BRANCH,
      sha,
      committer: { name: 'mowing-app-bot', email: 'app-bot@oldwellco.com' },
      author: { name: 'mowing-app-bot', email: 'app-bot@oldwellco.com' },
    }),
  });
  if (!r.ok) {
    const err = new Error(`DELETE ${path}: ${r.status} ${await r.text()}`);
    err.status = r.status;
    throw err;
  }
  return { deleted: true };
}

function setCors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Access-Control-Max-Age', '86400');
}

function assertConfigured(res) {
  if (!TOKEN) {
    res.status(500).json({ error: 'GITHUB_TOKEN env var not set on the Vercel project' });
    return false;
  }
  return true;
}

module.exports = { getFile, putFile, deleteFile, setCors, assertConfigured, REPO, BRANCH };

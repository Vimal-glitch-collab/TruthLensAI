import { mkdirSync, copyFileSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export function buildFrontend(root, output, backend = '') {
  if (backend) {
    const url = new URL(backend);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
      throw new Error('TRUTHLENS_BACKEND_ORIGIN must be an HTTPS origin without credentials, path, query or fragment.');
    }
    backend = url.origin;
  }
  const assets = resolve(output, 'static');
  mkdirSync(assets, { recursive: true });
  for (const name of ['index.html', 'app.js', 'style.css', 'upgrades.css', 'review.css', 'favicon.svg']) {
    copyFileSync(resolve(root, 'web', name), resolve(assets, name));
  }
  const index = resolve(assets, 'index.html');
  writeFileSync(index, readFileSync(index, 'utf8')
    .replaceAll('Media is processed locally.', 'Uploads are processed on our hosted analysis server.')
    .replaceAll('Local workspace', 'Cloud workspace')
    .replaceAll('Create a local account', 'Create an account')
    .replaceAll('The first local account receives existing reports from version 1.', 'Reports and retained image excerpts are stored on the hosted server.')
    .replaceAll('Local image, video, and article analysis', 'Image, video, and article analysis')
    .replaceAll('checked locally against', 'checked on the server against')
    .replaceAll('verified locally against', 'verified on the server against'));
  writeFileSync(resolve(assets, 'backend-unavailable.json'), JSON.stringify({error:'The hosted analysis backend is not connected yet. Please try again after deployment is complete.'}));
  const routes = [
    { src: '/(.*)', headers: {
      'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    }, continue: true },
    { src: '/api(?:/(.*))?', headers: {'Cache-Control':'no-store'}, ...(backend ? { dest: `${backend}/api/$1` } : { dest:'/backend-unavailable.json', status:503 }) },
    { handle:'filesystem' },
  ];
  writeFileSync(resolve(output, 'config.json'), JSON.stringify({version:3, routes}, null, 2));
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  buildFrontend(root, resolve(root, '.vercel/output'), process.env.TRUTHLENS_BACKEND_ORIGIN || '');
  console.log('Built static frontend and API proxy configuration.');
}

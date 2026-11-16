import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildFrontend } from '../tools/build_frontend.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

test('frontend publishes only web assets with same-origin API proxy', () => {
  const output = mkdtempSync(resolve(tmpdir(), 'truthlens-build-'));
  try {
    buildFrontend(root, output, 'https://api.example.test');
    const config = JSON.parse(readFileSync(resolve(output, 'config.json'), 'utf8'));
    assert.equal(config.version, 3);
    assert.equal(config.routes[1].dest, 'https://api.example.test/api/$1');
    assert.equal(config.routes[1].headers['Cache-Control'], 'no-store');
    assert.deepEqual(readdirSync(resolve(output, 'static')).sort(), ['app.js','backend-unavailable.json','favicon.svg','index.html','review.css','style.css','upgrades.css']);
    const html = readFileSync(resolve(output, 'static/index.html'), 'utf8');
    assert.match(html, /Uploads are processed on our hosted analysis server/);
    assert.doesNotMatch(html, /Media is processed locally/);
  } finally { rmSync(output, { recursive:true, force:true }); }
});

test('missing backend returns an explicit unavailable response', () => {
  const output = mkdtempSync(resolve(tmpdir(), 'truthlens-build-'));
  try {
    buildFrontend(root, output);
    const config = JSON.parse(readFileSync(resolve(output, 'config.json'), 'utf8'));
    assert.equal(config.routes[1].status, 503);
    assert.equal(config.routes[1].dest, '/backend-unavailable.json');
  } finally { rmSync(output, { recursive:true, force:true }); }
});

test('backend origin cannot contain credentials or injected paths', () => {
  for (const value of ['http://api.example.test','https://user:secret@api.example.test','https://api.example.test/path','https://api.example.test/?key=secret','https://api.example.test/#fragment']) {
    assert.throws(() => buildFrontend(root, 'unused', value));
  }
});

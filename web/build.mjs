// Bundles the app with esbuild into dist/ (PWA) or dist-inline/ (single-file preview).
import { build, context } from 'esbuild';
import { cp, mkdir, readFile, writeFile, rm, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

const args = new Set(process.argv.slice(2));
const inline = args.has('--inline');
const watch = args.has('--watch');
const out = inline ? 'dist-inline' : 'dist';
const FONTS = [
  ['@fontsource/ibm-plex-sans', 'ibm-plex-sans-latin-400-normal.woff2'],
  ['@fontsource/ibm-plex-sans', 'ibm-plex-sans-latin-500-normal.woff2'],
  ['@fontsource/ibm-plex-sans', 'ibm-plex-sans-latin-600-normal.woff2'],
  ['@fontsource/ibm-plex-mono', 'ibm-plex-mono-latin-400-normal.woff2'],
  ['@fontsource/ibm-plex-mono', 'ibm-plex-mono-latin-500-normal.woff2'],
];

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

// fonts are copied next to the css (dist/fonts/...) unless we inline them as data URIs
for (const [pkg, file] of FONTS) {
  await mkdir(path.join(out, 'fonts'), { recursive: true });
  await cp(path.join('node_modules', pkg, 'files', file), path.join(out, 'fonts', file));
}

const common = {
  entryPoints: ['src/main.js'],
  bundle: true,
  minify: !watch,
  sourcemap: false,
  target: ['es2021'],
  define: { __ARTIFACT__: inline ? 'true' : 'false' },
  loader: { '.woff2': inline ? 'dataurl' : 'file' },
  legalComments: 'none',
  logLevel: 'info',
};

const opts = inline
  ? { ...common, format: 'iife', outfile: path.join(out, 'app.js'), external: [] }
  : { ...common, format: 'iife', outfile: path.join(out, 'app.js'), external: ['*.woff2'] };

// CSS references ./fonts/x.woff2: keep that path for the PWA, inline for the preview.
const fontPlugin = {
  name: 'fonts',
  setup(b) {
    if (!inline) return;
    b.onResolve({ filter: /\.woff2$/ }, a => ({ path: path.resolve('node_modules', a.path.includes('plex-mono') ? '@fontsource/ibm-plex-mono' : '@fontsource/ibm-plex-sans', 'files', path.basename(a.path)) }));
  },
};
opts.plugins = [fontPlugin];

if (watch) {
  const ctx = await context(opts);
  await ctx.watch();
  console.log('watching…');
} else {
  await build(opts);
}

const js = await readFile(path.join(out, 'app.js'));
const css = await readFile(path.join(out, 'app.css'));
const hash = createHash('sha1').update(js).update(css).digest('hex').slice(0, 10);

if (inline) {
  // Single HTML fragment for the artifact preview: styles + app + script. Data is fetched from ./data/.
  const body = `<title>Acumen</title>\n<style>${css.toString()}</style>\n<div id="app"></div>\n<script>${js.toString().replace(/<\/script>/g, '<\\/script>')}</script>\n`;
  await writeFile(path.join(out, 'preview.html'), body);
  console.log('preview.html', (body.length / 1024).toFixed(0) + ' KB');
} else {
  let html = await readFile('src/index.html', 'utf8');
  html = html.replaceAll('__BUILD__', hash);
  await writeFile(path.join(out, 'index.html'), html);
  await cp('public', out, { recursive: true });
  // service worker with the build hash and the list of shell files to precache
  let sw = await readFile('public/sw.js', 'utf8');
  const core = ['./', 'index.html', `app.js?v=${hash}`, `app.css?v=${hash}`, 'manifest.webmanifest'];
  const extra = ['icons/icon.svg', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png', ...FONTS.map(f => 'fonts/' + f[1])];
  sw = sw.replace('__BUILD__', hash).replace('__CORE__', JSON.stringify(core)).replace('__EXTRA__', JSON.stringify(extra));
  await writeFile(path.join(out, 'sw.js'), sw);
  console.log('build', hash, 'js', (js.length / 1024).toFixed(0) + ' KB', 'css', (css.length / 1024).toFixed(0) + ' KB');
}

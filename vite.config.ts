import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const pdfRoot = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json'));
const pdfResources = new Map(['cmaps', 'standard_fonts'].flatMap(folder => readdirSync(join(pdfRoot, folder)).map(file => [`${folder}/${file}`, join(pdfRoot, folder, file)] as const)));

export default defineConfig({
  // The packaged Electron app loads index.html with file://, so generated assets
  // must use relative URLs instead of the web-root paths used by a hosted site.
  base: './',
  // React's development preamble needs an inline script. Keep the packaged
  // document's stricter CSP and permit the preamble only in the dev server.
  plugins: [react(), {
    name: 'local-pdf-resources',
    generateBundle() {
      for (const [name, filename] of pdfResources) this.emitFile({ type: 'asset', fileName: `pdf-assets/${name}`, source: readFileSync(filename) });
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = req.url?.split('?')[0].replace(/^\/pdf-assets\//, '');
        const filename = name && pdfResources.get(name);
        if (!req.url?.startsWith('/pdf-assets/') || !filename) { next(); return; }
        res.setHeader('Content-Type', 'application/octet-stream');
        res.end(readFileSync(filename));
      });
    },
  }, {
    name: 'development-csp',
    apply: 'serve',
    transformIndexHtml(html) {
      return html.replace("script-src 'self';", "script-src 'self' 'unsafe-inline';");
    },
  }],
  server: { host: '127.0.0.1', port: 5173, strictPort: true }
});

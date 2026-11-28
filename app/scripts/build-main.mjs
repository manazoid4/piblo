import { build } from 'esbuild';
await build({
  entryPoints: { main: 'electron/main.ts', preload: 'electron/preload.ts' },
  outdir: 'dist/main',
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'cjs',
  external: ['electron', 'node-pty', 'velopack'],
  sourcemap: true,
  logLevel: 'info',
});

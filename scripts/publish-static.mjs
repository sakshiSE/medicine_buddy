import { cp, mkdir, rm } from 'node:fs/promises';

// Vercel serves the public/ directory directly through its CDN.
// SvelteKit builds to build/; copy that output into public/ after the build.
await rm('public', { recursive: true, force: true });
await mkdir('public', { recursive: true });
await cp('build', 'public', { recursive: true });
console.log('SvelteKit static site copied to public/ for Vercel CDN hosting.');

import { defineConfig } from 'wxt';

/** Set by compose.yaml: no browser to launch, and the dev server must be reachable from the host. */
const inDocker = process.env.WXT_DOCKER === '1';

export default defineConfig({
  // Visible folder so it shows up in the browser's "Load unpacked" file picker.
  outDir: 'dist',
  manifest: {
    name: 'Trust Verifier',
    description:
      'Crowdsourced content trust verification: flag misinformation and manipulative content, and see what others have flagged.',
    permissions: ['storage'],
  },
  webExt: { disabled: inDocker },
  dev: inDocker ? { server: { host: '0.0.0.0', port: 3000, origin: 'http://localhost:3000' } } : undefined,
});

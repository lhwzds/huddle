import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { basePath } from './src/base';

const API = 'api';

export default defineConfig(({ mode }) => {
  // The app may be served under a prefix ('/huddle/'); the proxy in front of
  // it strips that prefix, but the browser's URLs keep it.
  const env = loadEnv(mode, '.', '');
  const base = basePath(env.HUDDLE_BASE_PATH);
  const root = base === '/' ? '' : base.replace(/\/+$/, '');

  return {
    base,
    plugins: [react()],
    server: {
      // The dev server talks to the backend the same way the proxy does.
      proxy: {
        [`${root}/${API}`]: {
          target: 'http://localhost:8000',
          rewrite: (path: string) => (root ? path.replace(root, '') : path),
        },
      },
    },
  };
});

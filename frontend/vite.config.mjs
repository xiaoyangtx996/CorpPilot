import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:7892', changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (outgoing, incoming) => {
            // Preserve rejection for foreign origins; adapt only our own dev UI.
            if (incoming.headers.origin === `http://${incoming.headers.host}`) {
              outgoing.setHeader('Origin', 'http://127.0.0.1:7892');
            }
          });
        },
      },
    },
  },
});

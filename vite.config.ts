import { defineConfig } from 'vite';
import { cloudflare } from '@cloudflare/vite-plugin';
import { sites } from './build/sites-vite-plugin';
import { resolve } from 'node:path';
process.env.CLOUDFLARE_CF_FETCH_ENABLED ??= 'false';
process.env.WRANGLER_SEND_METRICS ??= 'false';
process.env.WRANGLER_LOG_PATH ??= '.sites-runtime/wrangler/logs';
export default defineConfig({
  publicDir: false,
  plugins: [sites({mockAuth:false}), cloudflare({viteEnvironment:{name:'worker'},inspectorPort:false,config:{
    name:'ai-platform-feedreader',main:'./worker/index.js',compatibility_date:'2026-05-15',compatibility_flags:['nodejs_compat'],
    assets:{binding:'ASSETS',not_found_handling:'none',html_handling:'none',run_worker_first:true},
    d1_databases:[{binding:'DB',database_name:'ai-platform-feedreader-d1',database_id:'00000000-0000-4000-8000-000000000000'}],
    r2_buckets:[{binding:'IMAGES',bucket_name:'ai-platform-feedreader-r2'}]
  }})],
  build: { outDir: resolve('dist'), emptyOutDir: false },
  environments: {client: {build: {cssCodeSplit: false,
    rollupOptions: { input: { main: resolve('reader/index.html'), standalone: resolve('reader/standalone.js') },
      output: { assetFileNames: asset => asset.names?.some(name => name.endsWith('.css')) ? 'assets/reader.css' : 'assets/[name]-[hash][extname]', entryFileNames: chunk => chunk.name === 'standalone' ? 'standalone.js' : 'assets/[name]-[hash].js' } }
  }}}
});

import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
export async function sitesEnvironment(root = process.cwd()) {
  const runtime = resolve(root, '.sites-runtime');
  await mkdir(runtime, { recursive: true });
  process.env.CLOUDFLARE_CF_FETCH_ENABLED = 'false';
  process.env.WRANGLER_SEND_METRICS = 'false';
  process.env.WRANGLER_WRITE_LOGS = 'false';
  process.env.WRANGLER_LOG_PATH = resolve(runtime, 'wrangler/logs');
  process.env.WRANGLER_REGISTRY_PATH = resolve(runtime, 'wrangler/registry');
  process.env.MINIFLARE_REGISTRY_PATH = resolve(runtime, 'wrangler/registry');
  process.env.XDG_CONFIG_HOME = resolve(runtime, 'config');
  await mkdir(process.env.XDG_CONFIG_HOME, {recursive:true});
  return { root, runtime, client: resolve(root, 'dist/client'), worker: resolve(root, 'dist/worker') };
}

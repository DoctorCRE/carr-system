// Same class/config contract as tools/judge/interface.py. Runtime cannot switch.
import config from './judge-providers.v1.json' with { type: 'json' };
import { ToolError } from './tool-error.js';

export function providerFor(workClass, routing = config) {
  if (routing?.schema !== 'carr-judge-providers/v1' || !routing.providers ||
      Object.keys(routing.providers).sort().join(',') !== 'app_runtime,system_work')
    throw new ToolError({ error: 'judge_config_invalid' });
  if (routing.providers.app_runtime !== 'jev')
    throw new ToolError({ error: 'judge_runtime_pinned', hint: 'app_runtime is pinned to jev; decisions routing is forbidden' });
  if (!['system_work', 'app_runtime'].includes(workClass))
    throw new ToolError({ error: 'judge_class_invalid' });
  const provider = routing.providers[workClass];
  if (!['jev', 'decisions'].includes(provider))
    throw new ToolError({ error: 'judge_provider_invalid' });
  return provider;
}

export async function decisionsProvider() {
  throw new ToolError({ error: 'judge_decisions_unavailable', hint: 'decisions contract not yet verified / no key' });
}

export function judgeBinding(jev, workClass = 'system_work', routing = config) {
  const provider = providerFor(workClass, routing);
  if (provider === 'jev') return jev; // Preserve null and the commit-only cache API.
  const ask = decisionsProvider;
  ask.cacheAfterCommit = async () => {};
  return ask;
}

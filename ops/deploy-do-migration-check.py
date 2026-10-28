#!/usr/bin/env python3
"""Permit automatic full deploys only for additive migrations and matching domains."""
import json
import os
import sys
import tomllib
import urllib.error
import urllib.request


def check(config, applied, account, base):
    migrations = config.get('migrations', [])
    tags = [migration['tag'] for migration in migrations]
    if applied and applied not in tags:
        raise ValueError(f'production migration tag {applied!r} is not declared')
    pending = migrations[tags.index(applied) + 1:] if applied else migrations
    for migration in pending:
        unsafe = set(migration) - {'tag', 'new_classes', 'new_sqlite_classes'}
        if unsafe:
            raise ValueError(f'migration {migration["tag"]!r} is not additive only: {", ".join(sorted(unsafe))}')
    for setting in ('workers_dev', 'preview_urls'):
        if config.get(setting) is not False:
            raise ValueError(f'{setting} must be false')
    if 'route' in config or 'triggers' in config:
        raise ValueError('singular route and triggers are not supported for automatic deploy')
    routes = config.get('routes', [])
    if not isinstance(routes, list) or any(not isinstance(route, dict) or route.get('custom_domain') is not True for route in routes):
        raise ValueError('only custom domain routes are supported for automatic deploy')
    expected = {route['pattern'] for route in routes}
    if not expected:
        raise ValueError('wrangler.toml declares no custom domains')
    actual = set()
    page = 1
    received = 0
    total = None
    while True:
        request = urllib.request.Request(f'{base}/accounts/{account}/workers/domains?page={page}&per_page=100', headers={'Authorization': 'Bearer ' + os.environ.get('CLOUDFLARE_API_TOKEN', '')})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        if data.get('success') is not True or not isinstance(data.get('result'), list):
            raise ValueError('Cloudflare domains API returned an unsuccessful or invalid response')
        for domain in data['result']:
            if not isinstance(domain, dict) or any(not isinstance(domain.get(key), str) or not domain[key] for key in ('service', 'environment', 'hostname')):
                raise ValueError('Cloudflare domains API returned a malformed domain')
            if domain['service'] == 'carr-mcp' and domain['environment'] == 'production':
                actual.add(domain['hostname'])
        info = data.get('result_info')
        if not isinstance(info, dict):
            raise ValueError('Cloudflare domains API returned invalid pagination')
        count, per_page, total_count = (info.get(key) for key in ('count', 'per_page', 'total_count'))
        if (type(info.get('page')) is not int or info['page'] != page
                or any(type(value) is not int for value in (count, per_page, total_count))
                or per_page < 1 or count != len(data['result']) or count > per_page
                or total_count < 0 or (total is not None and total != total_count)):
            raise ValueError('Cloudflare domains API returned invalid pagination')
        total = total_count
        received += count
        if received == total:
            break
        if received > total or count == 0 or page >= 10:
            raise ValueError('Cloudflare domains API returned incomplete pagination')
        page += 1
    if expected != actual:
        raise ValueError(f'custom domain mismatch; missing from production: {sorted(expected - actual)}; extra in production: {sorted(actual - expected)}')


def main():
    try:
        with open(sys.argv[1], 'rb') as file:
            config = tomllib.load(file)
        check(config, sys.argv[2], sys.argv[3], sys.argv[4])
    except (ValueError, KeyError, TypeError, OSError, urllib.error.URLError, json.JSONDecodeError, IndexError) as error:
        print(f'Durable Object migration automatic deploy refused: {error}', file=sys.stderr)
        return 1
    print('Additive Durable Object migrations and production custom domains verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

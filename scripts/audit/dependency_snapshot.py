"""Genera un snapshot de GitHub desde dependencias instaladas y manifiestos del commit.

No instala, publica, modifica alertas ni lee .env. La salida es JSON por stdout.
Usar después de instalar requirements/development.txt y requirements/local.txt.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata as metadata
import json
from pathlib import PurePosixPath
import subprocess

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

MANIFESTS = ('requirements.txt', 'requirements/base.txt',
             'requirements/development.txt', 'requirements/local.txt')


def snapshot(sha, ref):
    def read(path):
        return subprocess.check_output(['git', 'show', f'{sha}:{path}'], text=True, encoding='utf-8')

    def roots(path, visited=()):
        if path in visited:
            raise ValueError('Inclusión circular de requirements')
        result = []
        for line in read(path).splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if line.startswith('-r '):
                include = str(PurePosixPath(path).parent / line[3:].strip())
                result.extend(roots(include, (*visited, path)))
            else:
                req = Requirement(line)
                if req.marker is None or req.marker.evaluate():
                    result.append(req)
        return result

    manifests = {}
    for path in MANIFESTS:
        direct = roots(path)
        direct_names = {canonicalize_name(req.name) for req in direct}
        pending, visited, resolved = list(direct), set(), {}
        while pending:
            req = pending.pop()
            name = canonicalize_name(req.name)
            dist = metadata.distribution(name)
            if not req.specifier.contains(dist.version, prereleases=True):
                raise ValueError(f'Versión instalada incompatible: {name}')
            key = (name, frozenset(req.extras))
            if key in visited:
                continue
            visited.add(key)
            item = resolved.setdefault(name, {
                'package_url': f'pkg:pypi/{name}@{dist.version}',
                'relationship': 'direct' if name in direct_names else 'indirect',
                'scope': 'runtime', 'dependencies': [],
            })
            for value in dist.requires or ():
                child = Requirement(value)
                if child.marker is not None and not any(
                        child.marker.evaluate({'extra': extra}) for extra in ('', *req.extras)):
                    continue
                child_name = canonicalize_name(child.name)
                if child_name not in item['dependencies']:
                    item['dependencies'].append(child_name)
                pending.append(child)
        manifests[path] = {'name': path, 'file': {'source_location': path}, 'resolved': resolved}
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    return {
        'version': 0, 'sha': sha, 'ref': ref,
        'job': {'correlator': 'profit-python-installed', 'id': sha},
        'detector': {'name': 'profit-python-installed', 'version': '1.0.0',
                     'url': 'https://github.com/Jdarenas22/profit-studio'},
        'scanned': now, 'manifests': manifests,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--ref', default='refs/heads/main')
    args = parser.parse_args()
    print(json.dumps(snapshot(args.sha, args.ref)))

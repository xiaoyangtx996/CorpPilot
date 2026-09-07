"""Read credentials privately from attached stdin; never persist them in Docker config."""
import json
import os
import re
import subprocess
import sys


def _request_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(512001)
        if len(raw) > 512000:
            raise ValueError()
        request = json.loads(raw, object_pairs_hook=_request_object)
        if not isinstance(request, dict):
            raise ValueError()
        engine = request.get('engine', 'codex')
        if (engine == 'codex' and set(request) != {'prompt', 'model', 'api_key'}
                or engine == 'opencode' and set(request) != {'prompt', 'model', 'api_key', 'engine'}
                or engine not in ('codex', 'opencode')):
            raise ValueError()
        for key, maximum in [('prompt', 64000), ('model', 200), ('api_key', 4096)]:
            if not isinstance(request[key], str) or not request[key].strip() or len(request[key]) > maximum:
                raise ValueError()
        if engine == 'opencode':
            if not re.fullmatch(r'opencode/[A-Za-z0-9][A-Za-z0-9._-]*', request['model']):
                raise ValueError()
            # Respect system policy; never hide a managed configuration with a new HOME.
            if any(os.path.lexists('/etc/opencode/' + name) for name in ('opencode.json', 'opencode.jsonc')):
                print('OpenCode managed configuration detected; administrator review required', file=sys.stderr)
                return 125
            env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/home/worker', 'TMPDIR': '/tmp',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                   'OPENCODE_DISABLE_PROJECT_CONFIG': 'true', 'OPENCODE_DISABLE_CLAUDE_CODE': 'true',
                   'CORPPILOT_ZEN_KEY': request['api_key']}
            for name, folder in [('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                                 ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')]:
                env[name] = '/home/worker/' + folder
                os.makedirs(env[name], mode=0o700, exist_ok=True)
            env['OPENCODE_CONFIG_DIR'] = env['XDG_CONFIG_HOME']
            config = {'model': request['model'], 'small_model': request['model'],
                      'enabled_providers': ['opencode'], 'share': 'disabled', 'autoupdate': False, 'lsp': False,
                      'permission': {'*': 'deny', 'read': 'allow', 'glob': 'allow', 'grep': 'allow',
                                     'edit': 'allow', 'external_directory': 'deny'},
                      'provider': {'opencode': {'options': {'apiKey': '{env:CORPPILOT_ZEN_KEY}'}}}}
            env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
            argv = ['opencode', '--pure', '--log-level', 'ERROR', 'run', '--model', request['model'],
                    '--format', 'json', '--title', 'CorpPilot execution', '--dir', '/work']
            process = subprocess.run(argv, input=request['prompt'].encode('utf-8'), env=env, cwd='/work')
            return process.returncode if process.returncode >= 0 else 128 - process.returncode
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/home/worker',
               'CODEX_HOME': '/home/worker/.codex', 'TMPDIR': '/tmp',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'CODEX_API_KEY': request['api_key']}
        os.makedirs(env['CODEX_HOME'], mode=0o700, exist_ok=True)
        argv = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--json',
                # The fixed outer container supplies isolation; nested bubblewrap requires extra capabilities.
                '--sandbox', 'danger-full-access', '-c', 'approval_policy="never"',
                '--skip-git-repo-check', '--color', 'never',
                '--model', request['model'], '-C', '/work', '-c', 'shell_environment_policy.inherit="none"',
                '-c', 'project_root_markers=[]', '-c', 'allow_login_shell=false']
        for key, value in env.items():
            if key != 'CODEX_API_KEY':
                argv += ['-c', f'shell_environment_policy.set.{key}={json.dumps(value)}']
        process = subprocess.run([*argv, '-'], input=request['prompt'].encode('utf-8'), env=env, cwd='/work')
        return process.returncode if process.returncode >= 0 else 128 - process.returncode
    except Exception:
        print('Worker input or CLI startup failed', file=sys.stderr)
        return 125


if __name__ == '__main__':
    raise SystemExit(main())

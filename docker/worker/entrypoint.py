"""Read credentials privately from attached stdin; never persist them in Docker config."""
import json
import os
import subprocess
import sys


def main():
    try:
        raw = sys.stdin.buffer.read(512001)
        if len(raw) > 512000:
            raise ValueError()
        request = json.loads(raw)
        if set(request) != {'prompt', 'model', 'api_key'}:
            raise ValueError()
        for key, maximum in [('prompt', 64000), ('model', 200), ('api_key', 4096)]:
            if not isinstance(request[key], str) or not request[key].strip() or len(request[key]) > maximum:
                raise ValueError()
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

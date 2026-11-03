"""Diagnostic only: isolated pytest processes, with every failure retained."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion

workspace = Path(os.environ['GITHUB_WORKSPACE'])
output = workspace / 'diagnostics'
output.mkdir(exist_ok=True)
metadata = {'python': sys.version, 'pyqt': PYQT_VERSION_STR, 'qt_build': QT_VERSION_STR,
            'qt_runtime': qVersion(), 'windows': platform.platform(),
            'image_os': os.environ.get('ImageOS'), 'image_version': os.environ.get('ImageVersion')}
(output / 'environment.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
print(json.dumps(metadata), flush=True)
results = []
theme = 'tests/test_shell_ui.py::test_theme_switch_restyles_the_window'


def run(source, label, args):
    env = os.environ.copy()
    for key in ('NGPCRAFT_DATA_DIR', 'NGPCRAFT_SETTINGS', 'NGPCRAFT_NETLOG'):
        env.pop(key, None)
    env.update(QT_QPA_PLATFORM='offscreen', PYTHONFAULTHANDLER='1')
    command = [sys.executable, '-m', 'pytest', '-v', *args]
    start = time.monotonic()
    print(f'BEGIN {source}/{label}: {command}', flush=True)
    with (output / f'{source}-{label}.log').open('wb') as log:
        try:
            completed = subprocess.run(command, cwd=workspace / source, env=env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=600)
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code = 'timeout-600s'
    result = {'source': source, 'stage': label, 'command': command, 'returncode': code,
              'seconds': round(time.monotonic() - start, 2)}
    results.append(result)
    (output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('END ' + json.dumps(result), flush=True)
    # Keep crash traces visible without losing the rest of the diagnostic matrix.
    lines = (output / f'{source}-{label}.log').read_text(encoding='utf-8', errors='replace').splitlines()
    for line in lines[-12:]:
        print(line, flush=True)


# These exclusions are diagnostic comparisons, never acceptance substitutes.
for number in range(5):
    run('candidate', f'01-theme-{number+1}', [theme])
run('candidate', '02-storage', ['tests/test_macos_storage.py'])
run('candidate', '03-storage-then-theme', ['tests/test_macos_storage.py', theme])
run('candidate', '04-ui-without-storage', ['tests/test_shell_ui.py'])
run('candidate', '05-full-without-storage', ['--ignore=tests/test_macos_storage.py'])
run('candidate', '06-full', [])
for number in range(5):
    run('baseline', f'07-theme-{number+1}', [theme])
run('baseline', '08-ui', ['tests/test_shell_ui.py'])
run('baseline', '09-full', [])
# Run the requested mixed order in the same process while recording state only.
run('candidate', '10-state-mixed', ['-p', 'qt_pr3_state', 'tests/test_macos_storage.py', theme])
sys.exit(0 if all(r['returncode'] == 0 for r in results) else 1)

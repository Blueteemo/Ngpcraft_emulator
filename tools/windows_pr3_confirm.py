"""Same-host native lifetime reproduction and normal fixed full-suite acceptance."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion

root = Path(os.environ['GITHUB_WORKSPACE'])
controller = root / '.audit/tools'
output = root / 'diagnostics'
output.mkdir(exist_ok=True)
fixed = 'c22d8db4d98d45e9680becd0d262e145c602f6bd'
results=[]
(output/'environment.json').write_text(json.dumps({'python':sys.version,'pyqt':PYQT_VERSION_STR,
 'qt_build':QT_VERSION_STR,'qt_runtime':qVersion(),'windows':platform.platform(),
 'image':os.environ.get('ImageVersion'),'fixed':fixed},indent=2),encoding='utf-8')


def run(label, cwd, args, *, expected_crash=False, tracing=False, probe=False):
    env=os.environ.copy()
    for key in ('NGPCRAFT_DATA_DIR','NGPCRAFT_SETTINGS','NGPCRAFT_NETLOG','PYTHONPATH'):
        env.pop(key,None)
    env.update(QT_QPA_PLATFORM='offscreen',PYTHONFAULTHANDLER='1',NGPCRAFT_NO_UPNP='1')
    if probe:
        scratch=output/label
        scratch.mkdir(exist_ok=True)
        env['NGPCRAFT_SETTINGS']=str(scratch/'settings.ini')
        env['NGPCRAFT_NETLOG']=str(scratch/'netplay.log')
        env['PYTHONPATH']=str(cwd)
        command=[sys.executable,'-X','faulthandler',str(controller/'palette_gc_probe.py'),str(scratch),*args]
    else:
        command=[sys.executable,'-m','pytest','-v',*args]
    if tracing:
        env['PYTHONPATH']=str(controller)
        env['QT_PR3_GC_LOG']=str(output/'natural-gc.jsonl')
    start=time.monotonic()
    print('BEGIN',label,command,flush=True)
    with (output/f'{label}.log').open('wb') as log:
        try:
            code=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
        except subprocess.TimeoutExpired:
            code='timeout'
    logtext=(output/f'{label}.log').read_text(encoding='utf-8',errors='replace')
    accepted = (code != 0 and 'access violation' in logtext) if expected_crash else code==0
    result={'stage':label,'returncode':code,'expected_native_crash':expected_crash,
            'accepted':accepted,'seconds':round(time.monotonic()-start,2)}
    results.append(result)
    (output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print('END',json.dumps(result),flush=True)
    print('\n'.join(logtext.splitlines()[-8:]),flush=True)


run('01-upstream-closed-window-gc',root/'.baseline',['True'],probe=True,expected_crash=True)
run('02-original-closed-window-gc',root,['True'],probe=True,expected_crash=True)
# Natural unmodified full suite; outcome retained as diagnosis, not fixed acceptance.
run('03-original-full-gc-trace',root,['-p','qt_pr3_gc'],tracing=True)
subprocess.run(['git','fetch','origin',fixed,'--depth=1'],cwd=root,check=True)
subprocess.run(['git','checkout','--detach',fixed],cwd=root,check=True)
run('04-fixed-explicit-gc-enabled',root,['True'],probe=True)
run('05-fixed-explicit-gc-disabled',root,['False'],probe=True)
run('06-fixed-storage-theme-regression',root,['tests/test_macos_storage.py','tests/test_palette_lifetime.py',
 'tests/test_shell_ui.py::test_theme_switch_restyles_the_window'])
run('07-fixed-full-1',root,[])
run('08-fixed-full-2',root,[])
# No exclusions or diagnostic hooks apply to either fixed full suite.
required=[r for r in results if r['stage']!='03-original-full-gc-trace']
sys.exit(0 if all(r['accepted'] for r in required) else 1)

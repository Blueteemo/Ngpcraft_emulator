"""Diagnostic only: record natural GC inside a native layout callback."""
import gc
import json
import os
from pathlib import Path
import sys
from PyQt6 import sip


def record(phase, info):
    frame = sys._getframe()
    layouts, stack = [], []
    while frame:
        stack.append(frame.f_code.co_name)
        if frame.f_code.co_name == '_lay' and frame.f_code.co_filename.endswith('ngpc_shell.py'):
            obj = frame.f_locals.get('self')
            if obj is not None:
                layouts.append({'id': id(obj), 'deleted': sip.isdeleted(obj)})
        frame = frame.f_back
    if not layouts:
        return
    result = {'phase': phase, 'info': info, 'layouts': layouts, 'stack': stack}
    with Path(os.environ['QT_PR3_GC_LOG']).open('a', encoding='utf-8') as output:
        output.write(json.dumps(result) + '\n')


def pytest_configure(config):
    gc.callbacks.append(record)

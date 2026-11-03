"""Read-only diagnostic pytest plugin; deliberately no processEvents/GC/cleanup."""
import gc
import json
import os
from pathlib import Path
import sys


def pytest_runtest_setup(item):
    if not (item.nodeid.startswith('tests/test_shell_ui.py') or item.nodeid.startswith('tests/test_macos_storage.py')):
        return
    import ngpc_paths as paths
    import ngpc_settings as cfg
    import ngpc_shell as shell
    from PyQt6.QtWidgets import QApplication
    from PyQt6 import sip
    app = QApplication.instance()
    settings = cfg.make_settings()
    state = {'nodeid': item.nodeid, 'data_env': os.environ.get(paths.DATA_ENV),
             'settings_env': os.environ.get(cfg.SETTINGS_FILE_ENV), 'argv': sys.argv[:],
             'application_class_restored': shell.QApplication is QApplication,
             'managed_impl': [paths.managed.__module__, paths.managed.__name__],
             'data_root': str(paths.data_root()), 'shell_repo': str(shell.REPO),
             'settings_file': settings.fileName(), 'managed': paths.managed(),
             'application_id': id(app) if app else None, 'gc_count': gc.get_count()}
    layouts = []
    for obj in gc.get_objects():
        if isinstance(obj, shell.FlowLayout):
            layouts.append({'id': id(obj), 'deleted': sip.isdeleted(obj), 'pyowned': sip.ispyowned(obj),
                            'items': [{'type': type(it).__name__, 'deleted': sip.isdeleted(it),
                                       'pyowned': sip.ispyowned(it)} for it in obj._items]})
    state['layouts'] = layouts
    file = Path(os.environ['GITHUB_WORKSPACE']) / 'diagnostics/state.jsonl'
    with file.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(state, ensure_ascii=True) + '\n')

# Build both entry points from one dependency graph and one shared runtime.
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parents[1]
generated = root / 'build' / 'windows-assets'
datas = [(str(root / 'everytensor' / 'qml'), 'everytensor/qml'),
         (str(root / 'everytensor' / 'model-assets.json'), 'everytensor'),
         (str(generated / 'model-worker'), 'model-worker'),
         (str(root / 'everytensor' / 'office_open.ps1'), 'everytensor'),
         (str(root / 'models' / 'quick-semantic'), 'models/quick-semantic')]
for package in ('PySide6-Essentials', 'shiboken6', 'Pillow', 'PyMuPDF', 'numpy', 'tokenizers'):
    datas += copy_metadata(package)

a = Analysis([str(root / 'tools' / 'windows_entry.py')], pathex=[str(root)],
             datas=datas, binaries=[], hiddenimports=['PySide6.QtTest'],
             excludes=['torch', 'torchvision', 'transformers', 'sentence_transformers',
                       'tkinter', 'everytensor.ui', 'everytensor.widgets',
                       'everytensor.settings_ui', 'matplotlib', 'IPython', 'pytest'],
             noarchive=False)
pyz = PYZ(a.pure)
options = dict(exclude_binaries=True, debug=False, strip=False, upx=False,
               icon=str(generated / 'EveryTensor.ico'),
               version=str(generated / 'version.txt'))
gui = EXE(pyz, a.scripts, [], name='EveryTensor', console=False, **options)
cli = EXE(pyz, a.scripts, [], name='EveryTensor-console', console=True, **options)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='EveryTensor')

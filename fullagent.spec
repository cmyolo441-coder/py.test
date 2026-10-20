# -*- mode: python ; coding: utf-8 -*-
import os
import pathlib
import sys

# collect_all/collect_submodules IMPORT the package while the spec runs.
# CI's pyinstaller process did not have the repo root on sys.path, so both
# helpers returned nothing (log: "module 'fullagent' ... is not a package")
# and every lazily-imported subsystem was left out of the binary — the
# standalone build then died with ModuleNotFoundError: fullagent.mastermind.
_ROOT = globals().get("SPECPATH") or os.getcwd()
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PyInstaller.utils.hooks import collect_all, collect_submodules

# every fullagent subpackage — systemprompt.py and others importlib them
# dynamically, so PyInstaller cannot see them on its own
datas, binaries, hiddenimports = collect_all('fullagent')
hiddenimports += collect_submodules('fullagent')

# Belt and braces: enumerate the package files without importing anything,
# so a single import-time error can never silently drop modules again.
_pkg_dir = pathlib.Path(_ROOT) / 'fullagent'
hiddenimports += [f'fullagent.{p.stem}' for p in _pkg_dir.glob('*.py')
                  if p.stem != '__init__']
hiddenimports = sorted(set(hiddenimports))


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='fullagent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

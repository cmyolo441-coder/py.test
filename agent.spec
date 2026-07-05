# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['agent.commands.goal_command', 'agent.commands.builtin_commands', 'agent.commands.config_command', 'agent.commands.feature_commands', 'agent.commands.persona_command', 'agent.commands.session_commands', 'agent.commands.ui_commands', 'agent.goal_mode', 'agent.cancellation', 'agent.effort', 'agent.fake_detector', 'agent.plugins.loader', 'agent.providers.factory', 'agent.providers.anthropic_provider', 'agent.providers.gemini_provider', 'agent.providers.mistral_provider', 'agent.providers.ollama_provider', 'agent.providers.openai_provider', 'agent.providers.together_provider', 'agent.tools.builtins', 'agent.tools.registry', 'agent.tools.catalog', 'agent.effects', 'agent.themes'],
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
    name='agent',
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

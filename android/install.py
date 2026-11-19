#!/usr/bin/env python3
"""Instala o Dusk TV numa Android TV ou Google TV pela rede, ou no emulador, com o adb do Android SDK.

    python android/install.py 192.168.0.50          # instala ou atualiza na TV, e abre
    python android/install.py 192.168.0.50 --home   # e tenta deixar o Dusk TV como tela inicial (botão Home)
    python android/install.py emulador              # no emulador aberto no computador
    python android/install.py 192.168.0.50 --uninstall

Antes, na TV: Configurações → Sistema → Sobre → aperte 7 vezes em "Build do sistema operacional" para virar
desenvolvedor; depois, em Configurações → Sistema → Opções do desenvolvedor, ligue a "Depuração USB" (e a
"Depuração por rede", onde houver). Na primeira conexão, a TV pergunta se aceita este computador: aceite.
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'android'))
import build  # noqa: E402

PACKAGE, ACTIVITY = 'io.dusktv.home', 'io.dusktv.home/.MainActivity'


def adb_path():
    exe = 'adb.exe' if os.name == 'nt' else 'adb'
    for base in (os.environ.get('ANDROID_HOME', ''), os.environ.get('ANDROID_SDK_ROOT', ''),
                 os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk'),
                 os.path.join(os.path.expanduser('~'), 'Library', 'Android', 'sdk'),
                 os.path.join(os.path.expanduser('~'), 'Android', 'Sdk')):
        path = os.path.join(base, 'platform-tools', exe)
        if base and os.path.isfile(path):
            return path
    found = shutil.which('adb')
    if found:
        return found
    sys.exit('Não achei o adb. Ele vem no Android SDK (platform-tools), que o Android Studio instala, ou sozinho em '
             'https://developer.android.com/tools/releases/platform-tools.')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    adb = adb_path()
    if args[0] in ('emulador', 'emulator'):
        target = ['-e']
    else:
        if not re.match(r'^\d{1,3}(\.\d{1,3}){3}(:\d+)?$', args[0]):
            sys.exit(__doc__)
        address = args[0] if ':' in args[0] else args[0] + ':5555'
        out = subprocess.run([adb, 'connect', address], capture_output=True, text=True).stdout
        if 'connected' not in out:
            sys.exit('Não conectou em %s: %s\nA depuração USB (ou por rede) está ligada na TV? Ela mostrou um aviso para '
                     'aceitar este computador?' % (address, out.strip()))
        target = ['-s', address]

    def adb_run(*more):
        return subprocess.run([adb] + target + list(more), capture_output=True, text=True)

    if '--uninstall' in sys.argv:
        r = adb_run('uninstall', PACKAGE)
        print('Removido.' if 'Success' in r.stdout else 'A TV respondeu: ' + (r.stdout + r.stderr).strip())
        return

    if not os.path.exists(build.APK):
        build.build()
    print('Instalando ...')
    r = adb_run('install', '-r', build.APK)
    if 'Success' not in r.stdout:
        text = (r.stdout + r.stderr).strip()
        if 'INSTALL_FAILED_UPDATE_INCOMPATIBLE' in text:
            sys.exit('A TV tem um Dusk TV assinado com outra chave. Remova antes com --uninstall e instale de novo.')
        sys.exit('Não instalou:\n' + text)
    if '--home' in sys.argv:
        h = adb_run('shell', 'cmd', 'package', 'set-home-activity', ACTIVITY)
        if h.returncode == 0 and 'Error' not in h.stdout + h.stderr:
            print('O Dusk TV agora é a tela inicial (botão Home).')
        else:
            print('Esta TV não deixou trocar a tela inicial por aqui. Em várias, dá para escolher ao apertar Home, ou em '
                  'Configurações → Apps → Apps padrão → Tela inicial.')
    adb_run('shell', 'am', 'start', '-n', ACTIVITY)
    print('Instalado e aberto na TV.')


if __name__ == '__main__':
    main()

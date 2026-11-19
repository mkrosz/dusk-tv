#!/usr/bin/env python3
"""Gera o Dusk TV para Android TV e Google TV: DuskTV-Android.apk.

    python android/build.py            # DuskTV-Android.apk
    python android/build.py --release  # sem o web/config.local.js (a sua chave do Unsplash), para publicar

Sem Gradle: aapt2 (recursos e manifesto) -> javac -> d8 -> zipalign -> apksigner, como o build do Free My TV.
Precisa de um JDK 17+ e do Android SDK com build-tools e uma plataforma (android-34 ou mais nova), que o Android
Studio instala em %LOCALAPPDATA%\\Android\\Sdk (Windows), ~/Library/Android/sdk (macOS) ou ~/Android/Sdk (Linux).
Outra pasta: defina ANDROID_HOME.

O APK sai assinado com uma chave criada na primeira vez em ~/.dusktv/android.keystore. Guarde esse arquivo: o
Android só aceita uma atualização assinada pela mesma chave (sem ela, é preciso desinstalar antes).
"""
import glob
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, 'android')
WEB = os.path.join(ROOT, 'web')
APK = os.path.join(ROOT, 'DuskTV-Android.apk')
KEYSTORE = os.path.join(os.path.expanduser('~'), '.dusktv', 'android.keystore')
# Uma chave só deste app, guardada na pasta do usuário: a senha não protege nada além dela.
KEY_ALIAS, KEY_PASS = 'dusktv', 'dusktv-android'
WIN = os.name == 'nt'

_spec = importlib.util.spec_from_file_location('tizen_build', os.path.join(ROOT, 'tizen', 'build.py'))
tizen_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tizen_build)
BUNDLED = ('index.html', 'home.css', 'config.js', 'config.local.js', 'config.local.example.js', 'tv.js', 'home.js')


def version_key(name):
    return [int(n) for n in re.findall(r'\d+', name)]


def sdk():
    home = os.path.expanduser('~')
    for base in (os.environ.get('ANDROID_HOME', ''), os.environ.get('ANDROID_SDK_ROOT', ''),
                 os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk'),
                 os.path.join(home, 'Library', 'Android', 'sdk'), os.path.join(home, 'Android', 'Sdk')):
        if base and os.path.isdir(os.path.join(base, 'build-tools')):
            return base
    sys.exit('Não achei o Android SDK. Instale o Android Studio (https://developer.android.com/studio) e, no SDK '
             'Manager, os pacotes "Android SDK Build-Tools" e uma plataforma (Android 14 ou mais nova). Se ele está '
             'em outra pasta, defina ANDROID_HOME.')


def jdk_tool(name):
    """javac ou keytool: do JAVA_HOME, do PATH, do JDK instalado ou do Java que vem com o Android Studio."""
    exe = name + ('.exe' if WIN else '')
    places = [os.path.join(os.environ.get('JAVA_HOME', ''), 'bin', exe)]
    found = shutil.which(name)
    if found:
        places.append(found)
    places += sorted(glob.glob(r'C:\Program Files\Java\*\bin\\' + exe), reverse=True)
    places += [r'C:\Program Files\Android\Android Studio\jbr\bin\\' + exe,
               '/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/' + name]
    for p in places:
        if p and os.path.isfile(p):
            return p
    sys.exit('Não achei o %s. Instale um JDK 17 ou mais novo (o Android Studio já traz um) ou defina JAVA_HOME.' % name)


def run(args, what):
    r = subprocess.run(args, capture_output=True, text=True, shell=WIN and args[0].endswith('.bat'))
    if r.returncode != 0:
        sys.exit('%s falhou:\n%s' % (what, (r.stdout + r.stderr).strip()))
    return r


def assets(folder, release):
    """A tela (web/), com o index.html carregando o CSS e os scripts, como nos pacotes da Samsung e da LG."""
    with open(os.path.join(folder, 'index.html'), 'wb') as f:
        f.write(tizen_build.inlined_index(release))
    for base, _, names in os.walk(WEB):
        for name in names:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, WEB)
            if rel.replace(os.sep, '/') in BUNDLED:
                continue
            os.makedirs(os.path.join(folder, os.path.dirname(rel)), exist_ok=True)
            shutil.copyfile(path, os.path.join(folder, rel))


def build(release=False):
    base = sdk()
    bt = os.path.join(base, 'build-tools', sorted(os.listdir(os.path.join(base, 'build-tools')), key=version_key)[-1])
    jars = sorted(glob.glob(os.path.join(base, 'platforms', 'android-*', 'android.jar')), key=version_key)
    if not jars:
        sys.exit('Falta uma plataforma do Android no SDK. No SDK Manager do Android Studio, instale o Android 14 ou mais novo.')
    android_jar = jars[-1]
    ext = '.exe' if WIN else ''
    aapt2, zipalign = os.path.join(bt, 'aapt2' + ext), os.path.join(bt, 'zipalign' + ext)
    d8, apksigner = os.path.join(bt, 'd8' + ('.bat' if WIN else '')), os.path.join(bt, 'apksigner' + ('.bat' if WIN else ''))
    javac, keytool = jdk_tool('javac'), jdk_tool('keytool')

    work = tempfile.mkdtemp(prefix='dusktv-apk-')
    try:
        os.makedirs(os.path.join(work, 'assets'))
        assets(os.path.join(work, 'assets'), release)

        run([aapt2, 'compile', '--dir', os.path.join(HERE, 'res'), '-o', os.path.join(work, 'res.zip')], 'aapt2 compile')
        unsigned = os.path.join(work, 'app.apk')
        run([aapt2, 'link', '-o', unsigned, '-I', android_jar, '--manifest', os.path.join(HERE, 'AndroidManifest.xml'),
             '-A', os.path.join(work, 'assets'), '--min-sdk-version', '21', '--target-sdk-version', '35',
             os.path.join(work, 'res.zip')], 'aapt2 link')

        classes = os.path.join(work, 'classes')
        sources = glob.glob(os.path.join(HERE, 'src', '**', '*.java'), recursive=True)
        run([javac, '-source', '8', '-target', '8', '-bootclasspath', android_jar, '-Xlint:-options', '-encoding', 'UTF-8',
             '-d', classes] + sources, 'javac')
        dex = os.path.join(work, 'dex')
        os.makedirs(dex)
        run([d8, '--lib', android_jar, '--min-api', '21', '--release', '--output', dex] +
            glob.glob(os.path.join(classes, '**', '*.class'), recursive=True), 'd8')
        with zipfile.ZipFile(unsigned, 'a', zipfile.ZIP_DEFLATED) as z:
            z.write(os.path.join(dex, 'classes.dex'), 'classes.dex')

        aligned = os.path.join(work, 'aligned.apk')
        run([zipalign, '-f', '-p', '4', unsigned, aligned], 'zipalign')

        if not os.path.exists(KEYSTORE):
            os.makedirs(os.path.dirname(KEYSTORE), exist_ok=True)
            run([keytool, '-genkeypair', '-keystore', KEYSTORE, '-alias', KEY_ALIAS, '-keyalg', 'RSA', '-keysize', '2048',
                 '-validity', '10000', '-storepass', KEY_PASS, '-keypass', KEY_PASS, '-dname', 'CN=Dusk TV'], 'keytool')
            print('Chave nova para assinar o app: %s (guarde este arquivo).' % KEYSTORE)
        run([apksigner, 'sign', '--ks', KEYSTORE, '--ks-key-alias', KEY_ALIAS, '--ks-pass', 'pass:' + KEY_PASS,
             '--key-pass', 'pass:' + KEY_PASS, '--v4-signing-enabled', 'false', '--out', APK, aligned], 'apksigner')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(os.path.join(HERE, 'AndroidManifest.xml'), encoding='utf-8') as f:
        version = re.search(r'android:versionName="([^"]+)"', f.read()).group(1)
    print('Pronto: %s (versão %s, %d KB%s)' % (os.path.basename(APK), version, os.path.getsize(APK) // 1024,
                                            ', sem a chave do Unsplash' if release else ''))


if __name__ == '__main__':
    build('--release' in sys.argv)

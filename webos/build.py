#!/usr/bin/env python3
"""Gera o Dusk TV para TVs LG (webOS): DuskTV-LG.ipk, o pacote que o Modo Desenvolvedor da TV instala.

    python webos/build.py          # DuskTV-LG.ipk, em 1920 x 1080
    python webos/build.py --hd     # em 1280 x 720, para LGs que não são 4K (o install.py já faz isso sozinho)
    python webos/build.py --release  # sem o web/config.local.js (a sua chave do Unsplash), para publicar

Junta o app (web/, com o index.html carregando tudo num arquivo só, como no pacote da Samsung), o webos/appinfo.json,
os ícones e o serviço JS (webos/service), e empacota do jeito que o ares-package da LG faz (um arquivo ar com
control.tar.gz e data.tar.gz). Não precisa de nenhuma ferramenta da LG, só do Python 3. O .ipk da LG não é assinado.
Baseado no webos/build.py do Free My TV (MIT).
"""
import importlib.util
import io
import json
import os
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
IPK = os.path.join(ROOT, 'DuskTV-LG.ipk')

# O index.html com o CSS e os scripts dentro vem do build da Samsung, para os dois pacotes saírem iguais.
_spec = importlib.util.spec_from_file_location('tizen_build', os.path.join(ROOT, 'tizen', 'build.py'))
tizen_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tizen_build)

# Já vão dentro do index.html, ou não vão para a TV.
BUNDLED = ('index.html', 'home.css', 'config.js', 'config.local.js', 'config.local.example.js', 'tv.js', 'home.js')


def tar_gz(entries):
    """Um .tar.gz de [(nome, bytes, ou None para uma pasta)], do root, como o ares-package faz."""
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode='w:gz', format=tarfile.PAX_FORMAT) as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.mtime = now
            info.uid = info.gid = 0
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def ar(members):
    out = io.BytesIO()
    out.write(b'!<arch>\n')
    now = int(time.time())
    for name, data in members:
        out.write(('%-16s%-12d%-6d%-6d%-8s%-10d`\n' % (name, now, 0, 0, '100644', len(data))).encode('ascii'))
        out.write(data)
        if len(data) % 2:
            out.write(b'\n')
    return out.getvalue()


def ar_members(data):
    """[(nome, bytes)] de um arquivo ar, como ar() escreve."""
    members, at = [], 8
    while at + 60 <= len(data):
        name, size = data[at:at + 16].decode('ascii').strip().rstrip('/'), int(data[at + 48:at + 58])
        members.append((name, data[at + 60:at + 60 + size]))
        at += 60 + size + size % 2
    return members


def _is_appinfo(name):
    return name.startswith('usr/palm/applications/') and name.endswith('/appinfo.json') and name.count('/') == 4


def resolution(ipk):
    """A resolução que o appinfo.json de um .ipk pede ('' se nenhuma)."""
    for name, data in ar_members(ipk):
        if name == 'data.tar.gz':
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                for member in tar.getmembers():
                    if _is_appinfo(member.name):
                        return json.loads(tar.extractfile(member).read().decode('utf-8')).get('resolution', '')
    return ''


def with_resolution(ipk, size):
    """O mesmo .ipk pedindo outra resolução (o install.py usa, para LGs que não são 4K)."""
    members = []
    for name, data in ar_members(ipk):
        if name == 'data.tar.gz':
            entries = []
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                for member in tar.getmembers():
                    body = None if member.isdir() else tar.extractfile(member).read()
                    if _is_appinfo(member.name):
                        body = json.dumps(dict(json.loads(body.decode('utf-8')), resolution=size), indent=2).encode('utf-8')
                    entries.append((member.name, body))
            data = tar_gz(entries)
        members.append((name, data))
    return ar(members)


def app_files(release=False):
    """{caminho no app: bytes}: o index.html com tudo dentro, as imagens, logos e fontes de web/, e os da LG."""
    files = {'index.html': tizen_build.inlined_index(release)}
    for folder, _, names in os.walk(WEB):
        for name in sorted(names):
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, WEB).replace(os.sep, '/')
            if rel not in BUNDLED:
                with open(path, 'rb') as f:
                    files[rel] = f.read()
    for name in ('appinfo.json', 'icon.png', 'largeIcon.png'):
        with open(os.path.join(ROOT, 'webos', name), 'rb') as f:
            files[name] = f.read()
    return files


def build(hd=False, release=False):
    with open(os.path.join(ROOT, 'webos', 'appinfo.json'), encoding='utf-8') as f:
        info = json.load(f)
    app_id, version = info['id'], info['version']
    app = app_files(release)
    if hd:   # LGs Full HD e HD desenham os apps em 1280 x 720; pedindo 1920 x 1080, algumas mostram só um canto
        app['appinfo.json'] = json.dumps(dict(info, resolution='1280x720'), indent=2).encode('utf-8')

    base = 'usr/palm/applications/' + app_id
    entries = [('usr', None), ('usr/palm', None), ('usr/palm/applications', None), (base, None)]
    dirs = set()
    for path in sorted(app):
        parent = os.path.dirname(path)
        if parent and parent not in dirs:
            dirs.add(parent)
            entries.append((base + '/' + parent, None))
        entries.append((base + '/' + path, app[path]))

    # O serviço JS que fala com a API de controle remoto da TV quando a página não pode (webos/service).
    service_id = app_id + '.service'
    with open(os.path.join(ROOT, 'webos', 'service', 'service.js'), 'rb') as f:
        service = {'service.js': f.read(),
                   'package.json': json.dumps({'name': service_id, 'version': version, 'main': 'service.js'},
                                              indent=2).encode('utf-8'),
                   'services.json': json.dumps({'id': service_id, 'description': 'Dusk TV',
                                                'services': [{'name': service_id, 'description': 'Dusk TV'}]},
                                               indent=2).encode('utf-8')}
    service_base = 'usr/palm/services/' + service_id
    entries += [('usr/palm/services', None), (service_base, None)]
    entries += [(service_base + '/' + name, service[name]) for name in sorted(service)]

    package = json.dumps({'id': app_id, 'version': version, 'app': app_id, 'services': [service_id]},
                         indent=2).encode('utf-8')
    entries += [('usr/palm/packages', None), ('usr/palm/packages/' + app_id, None),
                ('usr/palm/packages/' + app_id + '/packageinfo.json', package)]

    size = sum(len(d) for d in app.values()) + sum(len(d) for d in service.values()) + len(package)
    control = ('Package: %s\nVersion: %s\nSection: misc\nPriority: optional\nArchitecture: all\nInstalled-Size: %d\n'
               'Maintainer: Dusk TV <https://github.com/mkrosz/dusk-tv>\nDescription: Uma tela inicial limpa para a TV.\n'
               'webOS-Package-Format-Version: 2\nwebOS-Packager-Version: 1.0.0\n' % (app_id, version, size))
    data = ar([('debian-binary', b'2.0\n'), ('control.tar.gz', tar_gz([('control', control.encode('utf-8'))])),
               ('data.tar.gz', tar_gz(entries))])
    with open(IPK, 'wb') as f:
        f.write(data)
    print('Pronto: %s (%s %s, %d KB%s%s)' % (os.path.basename(IPK), app_id, version, len(data) // 1024,
                                            ', 1280 x 720' if hd else '', ', sem a chave do Unsplash' if release else ''))


if __name__ == '__main__':
    build('--hd' in sys.argv, '--release' in sys.argv)

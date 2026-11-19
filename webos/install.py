#!/usr/bin/env python3
"""Instala o Dusk TV numa TV LG em Modo Desenvolvedor, pelo Wi-Fi.

    python webos/install.py 192.168.0.50              # instala ou atualiza, e abre
    python webos/install.py 192.168.0.50 --uninstall  # remove

Antes da primeira vez, na TV: instale o app "Developer Mode" da LG Content Store, entre com uma conta gratuita de
desenvolvedor da LG (webostv.developer.lge.com), ligue o Dev Mode Status (a TV reinicia) e depois o Key Server. O app
mostra o IP da TV e uma frase-senha (passphrase).

Na primeira vez, este script baixa a chave da TV pelo Key Server e pede a frase-senha (digitada aqui, não fica
guardada). A chave destravada fica em ~/.ssh/dusktv_webos_<IP>, então as atualizações não pedem nada. O app vai pela
rede (SSH, porta 9922) e é instalado pelo instalador da própria TV (com.webos.appInstallService), como as ferramentas
da LG fazem. Numa TV que não é 4K (Full HD ou HD), ele entra em 1280 x 720, o tamanho em que essas TVs desenham os
apps (--1080p: como foi gerado). Precisa do Python 3 e do OpenSSH (ssh e ssh-keygen, que já vêm no Windows 10 e 11).
Baseado no webos/install.py do Free My TV (MIT), que se baseia no FreeMyTvWebOS de danielthejoker18 (MIT).
"""
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'webos'))
import build  # noqa: E402  (lê e muda o appinfo.json do pacote)

IPK = build.IPK
with open(os.path.join(ROOT, 'webos', 'appinfo.json'), encoding='utf-8') as f:
    APP_ID = json.load(f)['id']
TEMP = '/media/developer/temp/dusktv.ipk'
# A identidade SSH da TV, guardada na primeira vez e conferida depois, num arquivo só nosso. Uma TV cujo Modo
# Desenvolvedor foi configurado de novo tem outra identidade e é recusada até a linha dela (ou o arquivo) ser apagada.
KNOWN_HOSTS = os.path.join(os.path.expanduser('~'), '.ssh', 'dusktv_known_hosts')
SSH_OPTS = ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + KNOWN_HOSTS, '-o', 'LogLevel=ERROR',
            '-o', 'HostKeyAlgorithms=+ssh-rsa', '-o', 'PubkeyAcceptedKeyTypes=+ssh-rsa', '-p', '9922']


def key_path(ip):
    return os.path.join(os.path.expanduser('~'), '.ssh', 'dusktv_webos_' + ip.replace('.', '_'))


def saved_key(ip):
    """A chave guardada para este IP ou, se não houver, a mais nova guardada: a chave é da TV, e o IP dela pode mudar."""
    path = key_path(ip)
    folder = os.path.dirname(path)
    if os.path.exists(path) or not os.path.isdir(folder):
        return path
    saved = [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith('dusktv_webos_') and '.' not in n]
    return max(saved, key=os.path.getmtime) if saved else path


def lock(path):
    """Só este usuário pode ler a chave: no macOS e Linux basta o chmod; o OpenSSH do Windows confere a ACL do arquivo."""
    os.chmod(path, 0o600)
    if os.name != 'nt':
        return
    # Pelo SID, não pelo nome (nomes com acento podem não decodificar no código de página do console).
    out = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True).stdout
    sid = re.search(rb'S-1-[0-9-]+', out)
    if sid:
        subprocess.run(['icacls', path, '/inheritance:r', '/grant:r', '*%s:F' % sid.group().decode()], capture_output=True)


def fetch_key(ip):
    """A chave SSH da TV, pelo Key Server, destravada com a frase-senha que a TV mostra."""
    path = key_path(ip)
    if os.path.exists(path):
        return path
    url = 'http://%s:9991/webos_rsa' % ip
    print('Pegando a chave da TV em %s ...' % url)
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            data = r.read()
    except Exception as e:
        saved = saved_key(ip)
        if saved != path:
            print('Usando a chave guardada desta TV no IP anterior (%s).' % os.path.basename(saved))
            return saved
        sys.exit('Não deu para falar com o Key Server (%s). No app Developer Mode da TV, ligue o Key Server.' % e)
    passphrase = getpass.getpass('Frase-senha (Passphrase) mostrada no Developer Mode da TV: ').strip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.write(data)
    tmp.close()
    lock(tmp.name)
    unlock = subprocess.run(['ssh-keygen', '-p', '-P', passphrase, '-N', '', '-f', tmp.name], capture_output=True, text=True)
    if unlock.returncode != 0:
        os.unlink(tmp.name)
        reason = (unlock.stderr.strip().splitlines() or [''])[-1]
        sys.exit('Essa frase-senha não destravou a chave (%s). Confira no Developer Mode da TV e tente de novo.' % reason)
    shutil.move(tmp.name, path)
    lock(path)
    return path


def ssh(ip, key, command, data=None, tty=False):
    args = ['ssh'] + (['-tt'] if tty else []) + SSH_OPTS + ['-i', key, 'prisoner@' + ip, command]
    return subprocess.run(args, input=data, capture_output=True, text=data is None)


def luna(ip, key, uri, payload, replies=1, wait=20):
    # O luna-send-pub não escreve nada sem terminal (-tt), e o instalador manda várias respostas. webOS antigos têm um
    # BusyBox cujo timeout só aceita -t SEGUNDOS. Alguns (webOS 6) recusam o terminal; lá o -i manda as respostas sem ele.
    timeout = "if timeout -t 1 true 2>/dev/null; then T='timeout -t %d'; else T='timeout %d'; fi; " % (wait, wait)
    message = "%s '%s'" % (uri, json.dumps(payload, separators=(',', ':')))
    out = ssh(ip, key, timeout + '$T luna-send-pub -n %d %s' % (replies, message), tty=True).stdout or ''
    if not out.strip():
        out = ssh(ip, key, timeout + '$T luna-send-pub -i %s </dev/null' % message).stdout or ''
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args or not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', args[0]):
        sys.exit(__doc__)
    ip = args[0]
    if not shutil.which('ssh') or not shutil.which('ssh-keygen'):
        sys.exit('É preciso o OpenSSH (ssh e ssh-keygen). No Windows: Configurações → Sistema → Recursos opcionais → '
                 'Cliente OpenSSH.')
    key = fetch_key(ip)

    if '--uninstall' in sys.argv:
        out = luna(ip, key, 'luna://com.webos.appInstallService/dev/remove', {'id': APP_ID}, replies=3, wait=30)
        print('Removido.' if '"returnValue":true' in out else 'A TV respondeu: ' + out.strip())
        return

    if not os.path.exists(IPK):
        build.build()
    with open(IPK, 'rb') as f:
        package = f.read()
    if '--1080p' not in sys.argv and build.resolution(package) != '1280x720':
        info = luna(ip, key, 'luna://com.webos.service.tv.systemproperty/getSystemInfo', {'keys': ['UHD']}, wait=5)
        if re.search(r'"UHD"\s*:\s*"?false', info):
            package = build.with_resolution(package, '1280x720')
            print('Esta TV não é 4K, então o app entra em 1280 x 720, o tamanho em que ela desenha os apps.')
    print('Copiando o app para a TV ...')
    copy = ssh(ip, key, 'mkdir -p /media/developer/temp && cat > ' + TEMP, data=package)
    if copy.returncode != 0:
        err = copy.stderr.decode('utf-8', 'replace') if isinstance(copy.stderr, bytes) else copy.stderr
        if 'host key' in err.lower() or 'IDENTIFICATION HAS CHANGED' in err:
            sys.exit('A TV respondeu com outra identidade SSH do que a guardada para %s. Se o Modo Desenvolvedor foi '
                     'configurado de novo na TV, apague %s e tente outra vez.' % (ip, KNOWN_HOSTS))
        sys.exit('Não deu para copiar: %s\nO Modo Desenvolvedor ainda está ligado? Se a chave mudou, apague %s e tente '
                 'de novo.' % (err.strip(), key))
    print('Instalando ...')
    out = luna(ip, key, 'luna://com.webos.appInstallService/dev/install', {'id': APP_ID, 'ipkUrl': TEMP, 'subscribe': True},
               replies=30, wait=60)
    if '"state":"installed"' not in out:
        sys.exit('A TV não terminou de instalar. Ela respondeu:\n' + out.strip())
    ssh(ip, key, 'rm -f ' + TEMP)
    luna(ip, key, 'luna://com.webos.applicationManager/launch', {'id': APP_ID})
    print('Instalado e aberto na TV.')
    print('O Modo Desenvolvedor da LG dura uns 40 dias: de vez em quando, abra o app Developer Mode e aperte EXTEND.')


if __name__ == '__main__':
    main()

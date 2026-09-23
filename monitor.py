"""
Monitor de vagas em atividades do Even3.

Verifica de tempos em tempos as atividades escolhidas. Quando uma vaga abre,
inscreve automaticamente ou avisa no celular, conforme configurado em ALVOS.

    python monitor.py                      roda o monitor
    python monitor.py --listar [texto]     lista as atividades e seus ids
    python monitor.py --teste-notificacao  envia uma notificacao de teste
    python monitor.py --teste-inscricao ID inscreve e cancela, para validar
    python monitor.py --teste-token        confere o formato do token de login
"""
import json
import random
import re
import sys
import time
import chompjs
import requests
from datetime import datetime
from pathlib import Path


PASTA = Path(__file__).parent
ARQ_COOKIE = PASTA / ".cookie"   # obrigatorio: o cabecalho Cookie do navegador
ARQ_NTFY = PASTA / ".ntfy"       # obrigatorio: o nome do topico do ntfy.sh
ARQ_LOGIN = PASTA / ".login"     # opcional: {"email": "...", "senha": "..."}

BASE = "https://www.even3.com.br"
DOMINIO = "www.even3.com.br"
URL_ATIVIDADES = f"{BASE}/participante/sessions/"
URL_INSCRICAO = f"{URL_ATIVIDADES}realizarinscricao"
URL_CANCELAMENTO = f"{URL_ATIVIDADES}cancelaratividadegratis"
URL_LOGIN = f"{BASE}/evento/login/"
URL_LOGIN_POST = f"{BASE}/evento/loginusuario"

INTERVALO = 60  # segundos entre uma verificacao e outra
ESPERA_ENTRE_LOGINS = 20 * 60

# Quais atividades vigiar. Rode --listar para descobrir os ids do seu evento.
#   id da atividade: (nome curto para as mensagens, inscrever automaticamente)
# Use True quando a vaga for so sua; use False quando a inscricao exigir uma
# decisao na hora, como desmarcar outra atividade no mesmo horario.
ALVOS = {
    # 1234567: ("Palestra de IA", True),    # inscreve sozinho quando abrir vaga
    # 7654321: ("Workshop", False),         # so manda notificacao
}

# copia dos cabecalhos que o navegador manda; foi assim que o site aceitou os pedidos
sessao = requests.Session()
sessao.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:155.0) Gecko/20100101 Firefox/155.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE,
    "Referer": URL_ATIVIDADES,
    "Priority": "u=0",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
})


def log(mensagem):
    """Escreve no terminal com a hora na frente"""
    print(f"[{datetime.now():%d/%m %H:%M:%S}] {mensagem}", flush=True)


def notificar(titulo, mensagem, prioridade="urgent"):
    """Envia uma notificacao para o celular pelo topico gravado em .ntfy"""
    try:
        requests.post(
            f"https://ntfy.sh/{ARQ_NTFY.read_text().strip()}",
            data=mensagem.encode("utf-8"),
            headers={"Title": titulo, "Priority": prioridade, "Tags": "rotating_light"},
            timeout=10,
        )
    except Exception as erro:  # uma notificacao falha nao derruba monitor
        log(f"Falha ao notificar: {erro}")


def carregar_cookies():
    """Le o cabecalho Cookie copiado do navegador e entrega os cookies a sessao"""
    for parte in ARQ_COOKIE.read_text().split(";"):
        if "=" in parte:
            nome, valor = parte.strip().split("=", 1)
            # sem o dominio, a renovacao do servidor viraria um cookie separado
            # e a sessao passaria a mandar os dois valores para o site
            sessao.cookies.set(nome, valor, domain=DOMINIO, path="/")


def salvar_cookies():
    """Grava os cookies da sessao no arquivo, se o servidor tiver renovado algum"""
    atuais = dict(sessao.cookies.items())  # dict por nome: nunca grava repetido
    linha = "; ".join(f"{nome}={valor}" for nome, valor in atuais.items())
    if linha != ARQ_COOKIE.read_text():
        ARQ_COOKIE.write_text(linha)
        ARQ_COOKIE.chmod(0o600)


def como_json(conteudo):
    """Vira texto JSON sem espacos, byte a byte igual ao que o site envia"""
    return json.dumps(conteudo, separators=(",", ":"))


def checar_resposta(resposta):
    """Em erro, mostra o comeco do que o servidor respondeu, nao so o codigo"""
    if resposta.status_code >= 400:
        corpo = " ".join(resposta.text.split())[:300]
        raise RuntimeError(f"HTTP {resposta.status_code}: {corpo}")


def postar(url, corpo, referer=URL_ATIVIDADES):
    """Envia um POST em JSON e devolve a resposta; erro se o site recusar"""
    resposta = sessao.post(
        url,
        data=como_json(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json;charset=utf-8", "Referer": referer},
        timeout=30,
    )
    checar_resposta(resposta)

    # o site responde 200 mesmo quando recusa, e explica o motivo em Message
    dados = resposta.json()
    if not dados.get("IsValid"):
        raise RuntimeError(dados.get("Message") or "o site recusou o pedido")
    return dados


def buscar_atividades():
    """Devolve {id: atividade} a partir do objeto viewModel embutido na pagina,
    ou None quando a sessao expirou e o site devolveu a tela de login"""
    resposta = sessao.get(URL_ATIVIDADES, timeout=30)
    checar_resposta(resposta)

    marca = re.search(r"var viewModel\s*=\s*", resposta.text)
    if not marca:
        return None

    # chaves sem aspas: e um objeto JavaScript, nao JSON. O chompjs le o
    # primeiro objeto e para sozinho, ignorando o resto da pagina.
    dados = chompjs.parse_js_object(resposta.text[marca.end():])
    # a tela de login tambem tem um viewModel, mas sem a lista de atividades
    lista = dados.get("listaMinicursoParticipante")
    if lista is None:
        return None

    atividades = {}
    for atividade in lista:
        # o parser devolve os booleanos como texto, e "false" e verdadeiro em Python
        atividade["estouInscrito"] = str(atividade["estouInscrito"]).lower() == "true"
        atividade["inscritos"] = int(atividade["quantidadeInscritos"])
        atividade["limite"] = int(atividade["limiteDeVagas"])
        atividade["vagas"] = atividade["limite"] - atividade["inscritos"]
        atividades[int(atividade["idMinicurso"])] = atividade
    return atividades


def buscar_ou_parar():
    """Igual a buscar_atividades, mas para o programa se a sessao tiver expirado"""
    atividades = buscar_atividades()
    if atividades is None:
        sys.exit("Sessao expirada: cole um cookie novo em .cookie")
    return atividades


def inscrever(id_atividade):
    """Mesma chamada do botao "Realizar inscricao" do site"""
    # o site envia o JSON da inscricao dentro de um campo de texto chamado "model"
    pedido = como_json({"idsAtividade": [id_atividade], "cupomDesconto": None})
    dados = postar(URL_INSCRICAO, {"model": pedido})

    # aqui o site responde IsValid, mas avisa que bate com outra inscricao sua
    if dados.get("Action") == "CHOQUEHORARIO":
        conflitos = [c["titulo"] for c in dados["Object"][0]["chocadas"]]
        raise RuntimeError("choque de horario com: " + ", ".join(conflitos))


def cancelar(id_atividade):
    """Usado apenas no modo de teste: o monitor nunca cancela nada sozinho"""
    postar(URL_CANCELAMENTO, {"idAtividade": id_atividade})


def achar_token(pagina):
    """Pega o token antifalsificacao do campo escondido do formulario de login"""
    achado = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', pagina)
    if not achado:
        raise RuntimeError("token nao encontrado na pagina de login\n"
                           "(rode --teste-token para ver o que a pagina traz)")
    return achado.group(1)


def logar():
    """Refaz o login com as credenciais guardadas em .login"""
    credenciais = json.loads(ARQ_LOGIN.read_text())
    token = achar_token(sessao.get(URL_LOGIN, timeout=30).text)

    pedido = como_json({
        "email": credenciais["email"],
        "senha": credenciais["senha"],
        "returnUrl": None,
        "urlEvento": None,
        "_fb": None,
        "_er": None,
        "tokenReCaptcha": None,
        "tokenfp": None,
        "emailValidation": True,
    })
    postar(URL_LOGIN_POST, {"model": pedido, "requestVerificationToken": token}, referer=URL_LOGIN)

    # so confio no login depois de conseguir ler a pagina de novo
    if buscar_atividades() is None:
        raise RuntimeError("o site aceitou o login mas a sessao continua invalida")
    salvar_cookies()


def refazer_login():
    """Refaz o login. So e chamado de vez em quando: o site bloqueia a conta
    por alguns minutos depois de tentativas seguidas"""
    # sem o arquivo .login o monitor funciona igual, so nao se recupera sozinho
    if not ARQ_LOGIN.exists():
        notificar("Sessao expirada", "Cole um cookie novo em .cookie.", "high")
        return
    try:
        logar()
        log("Sessao renovada pelo login automatico")
    except Exception as erro:
        notificar("Login automatico falhou", f"{erro}. Cole um cookie novo em .cookie.", "high")


def verificar(atividades, pendentes, avisados):
    """Uma rodada: tira de pendentes o que ja foi resolvido"""
    status = []

    for id_atividade, (nome, automatico) in list(pendentes.items()):
        atividade = atividades.get(id_atividade)
        if atividade is None:
            status.append(f"{nome}: nao esta na lista")
            continue

        if atividade["estouInscrito"]:
            log(f"{nome}: inscrito, saindo da lista")
            notificar("Inscrito", f"Voce esta inscrito em {nome}.", "default")
            del pendentes[id_atividade]
            continue

        status.append(f"{nome}: {atividade['inscritos']}/{atividade['limite']}")

        if atividade["vagas"] <= 0:
            avisados.discard(id_atividade)  # esgotou de novo, pode avisar na proxima
            continue
        if id_atividade in avisados:  # ja tratou esta abertura de vaga
            continue
        avisados.add(id_atividade)

        if not automatico:
            notificar("Vaga aberta", f"{nome}: inscreva-se manualmente.")
            continue

        try:
            inscrever(id_atividade)
            # confere na pagina antes de cantar vitoria e parar de monitorar
            confirmacao = buscar_atividades() or {}
            if confirmacao.get(id_atividade, {}).get("estouInscrito"):
                log(f"{nome}: inscrito pelo bot")
                notificar("Inscrito automaticamente", f"{nome}: garantido!", "high")
                del pendentes[id_atividade]
            else:
                notificar("Vaga aberta", f"{nome}: o site aceitou mas nao confirmou. Confira!")
        except Exception as erro:
            notificar("Vaga aberta", f"{nome}: a inscricao automatica falhou ({erro}).")

    if status:
        log(" | ".join(status))


def main():
    """Verifica os alvos de tempos em tempos ate nao sobrar nada para monitorar"""
    if not ALVOS:
        sys.exit("Preencha ALVOS antes de rodar. Use --listar para ver os ids do evento.")

    carregar_cookies()
    pendentes = dict(ALVOS)
    avisados = set()
    erros = 0
    proxima_renovacao = 0.0

    log("Monitorando: " + ", ".join(nome for nome, automatico in pendentes.values()))
    while pendentes:
        try:
            atividades = buscar_atividades()
            if atividades is None:
                log("Sessao expirada")
                # reler o arquivo e de graca e sem risco, entao acontece toda volta
                carregar_cookies()
                if buscar_atividades() is not None:
                    log("Sessao renovada pelo cookie do arquivo")
                elif time.time() >= proxima_renovacao:
                    proxima_renovacao = time.time() + ESPERA_ENTRE_LOGINS
                    refazer_login()
            else:
                verificar(atividades, pendentes, avisados)
                salvar_cookies()
                erros = 0
        except Exception as erro:
            erros += 1
            log(f"Erro ({erros}): {erro}")
            if erros == 5:
                notificar("Monitor com erro", str(erro), "high")

        # o intervalo varia um pouco para nao bater no servidor sempre no mesmo segundo
        time.sleep(INTERVALO + random.uniform(0, 15))

    log("Nada mais para monitorar.")


if __name__ == "__main__":
    if "--listar" in sys.argv:
        carregar_cookies()
        seguinte = sys.argv.index("--listar") + 1
        termo = sys.argv[seguinte].lower() if seguinte < len(sys.argv) else ""

        for id_atividade, atividade in sorted(buscar_ou_parar().items()):
            if termo not in atividade["titulo"].lower():
                continue
            if atividade["estouInscrito"]:
                situacao = "inscrito"
            elif atividade["vagas"] > 0:
                situacao = f"{atividade['vagas']} vaga(s)"
            else:
                situacao = "esgotada"
            log(f"{id_atividade}  {atividade['inscritos']:>3}/{atividade['limite']:<4} "
                f"{situacao:>10}  {atividade['titulo'][:55]}"
                f"{'  <- alvo' if id_atividade in ALVOS else ''}")

    elif "--teste-notificacao" in sys.argv:
        notificar("Teste", "Se chegou no celular, esta funcionando.", "default")
        log("Notificacao enviada")

    elif "--teste-token" in sys.argv:
        # confere o formato do token sem tentar logar, que bloqueia a conta se repetir
        pagina = sessao.get(URL_LOGIN, timeout=30).text
        trechos = re.findall(r".{0,90}RequestVerificationToken.{0,90}", pagina, re.I)
        log(f"{len(trechos)} mencao(oes) ao token na pagina:")
        for trecho in trechos:
            log(f"  ...{' '.join(trecho.split())}...")
        log(f"o que o monitor extrairia: {achar_token(pagina)}")

    elif "--teste-inscricao" in sys.argv:
        carregar_cookies()
        id_teste = int(sys.argv[sys.argv.index("--teste-inscricao") + 1])
        inscrever(id_teste)
        log(f"inscrito: {buscar_ou_parar()[id_teste]['estouInscrito']} (esperado True)")
        cancelar(id_teste)
        log(f"inscrito: {buscar_ou_parar()[id_teste]['estouInscrito']} (esperado False)")

    else:
        main()

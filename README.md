# Monitor de vagas — Even3

Bot que vigia atividades esgotadas de um evento no [Even3](https://www.even3.com.br)
e age no segundo em que alguém cancela: se inscreve sozinho ou manda uma
notificação para o celular.

Escrito para a 28ª Semana Integrada da Escola Politécnica da PUC-Campinas, onde
as palestras de tecnologia esgotaram em algumas horas e as vagas só reapareciam
quando alguém desistia, funciona em qualquer evento hospedado no Even3, portanto
que a estrutura do site não seja mudada. Feito em python puro.

## Usando

### 1. Instale

```bash
pip install -r requirements.txt
```

### 2. Crie o arquivo `.cookie`

O bot vai agir como se fosse você, então precisa da sua sessão: Abra o evento no Even3 já
logado, aperte F12, vá na aba **Network** e recarregue a página. Clique na
primeira requisição, procure **Request Headers → Cookie**, clique com o botão
direito e escolha **Copy Value**. Cole numa linha só:

```bash
nano .cookie
chmod 600 .cookie
```

chmod para caso seja um computador compartilhado e/ou caso não queira vazar o cookie.

### 3. Crie o arquivo `.ntfy`

Instale o app [ntfy](https://ntfy.sh) no celular, toque em **+** e assine um
tópico com um nome difícil de adivinhar. Escreva o mesmo nome no arquivo:

```bash
nano .ntfy
python monitor.py --teste-notificacao
```

O nome do tópico é a única chave: quem souber dele recebe as suas notificações e
pode mandar mensagens para você, Por isso tente por algo dificil de chutar.

### 4. Descubra os ids das atividades

```bash
python monitor.py --listar
python monitor.py --listar hacking #filtra pelo título
```

```
1595393  100/100    esgotada  Vivendo de Hacking - Iniciando sua Carreira em Seg..
1596396  140/170    inscrito  As Novas Fronteiras da Cibersegurança no Contexto...
1595576    6/220  214 vaga(s)  O papel da liderança na Segurança...
```

O filtro compara o texto exato, então acentos contam: `segurança` acha,
`seguranca` não.

### 5. Preencha o `ALVOS`

```python
ALVOS = {
    1595283: ("Palestra de IA", True),    # inscreve sozinho quando abrir vaga
    1595393: ("Workshop", False),         # só manda notificação
    ID: ("NOME PARA IDENTIFICAR", True/False)
}
```

O segundo valor decide o que fazer quando a vaga abrir. Use `True` quando a vaga
for só sua e você quiser garantir dormindo. Use `False` quando a inscrição exigir
uma decisão na hora, por exemplo, quando a atividade bate com outra em que você
já está inscrito, e aceitar significa desmarcar a primeira.

### 6. Rode

```bash
python monitor.py
```

Para rodar por dias sem o notebook suspender(caso esteja usando):

```bash
systemd-inhibit --what=sleep:idle:handle-lid-switch python monitor.py
```

## O arquivo `.login` é opcional

Sem ele, o bot funciona normalmente. A diferença aparece quando a sessão do Even3
cai, o que acontece de tempos em tempos:

|                      | sem `.login`                              | com `.login`                     |
|----------------------|-------------------------------------------|----------------------------------|
| sessão cai           | manda notificação e espera você            | refaz o login e continua          |
| você precisa estar perto | sim, para colar um cookie novo         | não                               |
| rodar dias sem olhar | não                                        | sim                               |

No código isso é literalmente uma checagem de existência de arquivo:

```python
if not ARQ_LOGIN.exists():
    notificar("Sessao expirada", "Cole um cookie novo em .cookie.", "high")
    return
```

Arquivo ausente não é erro: o programa segue pelo outro caminho. Se quiser
ativar:

```bash
echo '{"email": "voce@exemplo.com", "senha": "sua-senha"}' > .login
chmod 600 .login
```

O preço é guardar a senha em texto puro no disco, protegida só pela permissão do
sistema de arquivos. Vale usar uma senha exclusiva do Even3, que você não
reaproveite em outro lugar.

Vale notar também que colar um cookie novo no `.cookie` funciona com o monitor
rodando, sem reiniciar nada: ele relê o arquivo a cada volta.

## Como cheguei na API

O Even3 não publica API. A página de atividades também não serve para raspagem
direta: ela é montada por JavaScript, então baixar o HTML com `requests` devolve
um documento vazio.

O caminho foi abrir o DevTools e olhar o que o site faz por baixo.

**Os dados já vêm na página.** Buscando o nome de uma palestra dentro das
respostas de rede, ele aparecia no meio do próprio HTML, dentro de um `<script>`,
num objeto `var viewModel = {...}` com todas as atividades do evento. Cada uma
traz `idMinicurso`, `limiteDeVagas`, `quantidadeInscritos` e `estouInscrito`.
Ou seja: nada de navegador automatizado, bastava uma requisição autenticada.

**Não é JSON.** É um objeto JavaScript: as chaves não têm aspas, então
`json.loads` não lê. A biblioteca [chompjs](https://github.com/Nykakin/chompjs)
resolve. Um detalhe que só descobri testando: ela para sozinha no fim do primeiro
objeto. Dá para jogar a página inteira nela que o resto é ignorado, o que
eliminou a necessidade de delimitar o objeto na mão.

**Inscrever é um POST simples.** Clicando em "Realizar inscrição" com a aba
Network aberta, aparece um `POST /participante/sessions/realizarinscricao` com um
corpo JSON de 63 bytes.

**O campo `model`.** Reproduzir esse POST deu erro 500 por um bom tempo. Os
cabeçalhos estavam iguais aos do navegador e o JSON parecia certo:

```json
{"idsAtividade":[1596449],"cupomDesconto":null}
```

Só que isso dá 47 bytes, e o navegador mandava 63. Os 16 bytes de diferença eram
a pista. Olhando o corpo real em modo Raw, o site não envia o JSON direto — ele
envia o JSON **como texto, dentro de um campo chamado `model`**:

```json
{"model":"{\"idsAtividade\":[1596449],\"cupomDesconto\":null}"}
```

63 bytes. Com esse formato, funcionou de primeira.

## Como testei

O site não tem ambiente de testes e cada tentativa de login errada bloqueia a
conta por alguns minutos. Isso empurrou o projeto para quatro níveis de
verificação.

**Modos de diagnóstico.** Cada peça pode ser validada isolada, sem rodar o
monitor inteiro:

```bash
python monitor.py --listar              # a leitura bate com o site?
python monitor.py --teste-notificacao   # a notificação chega no celular?
python monitor.py --teste-inscricao ID  # inscreve e cancela na mesma hora
python monitor.py --teste-token         # o login acha o token antifalsificação?
```

O `--teste-token` existe porque a extração do token era a única parte escrita no
escuro. Ele só baixa a página de login e mostra o que tem lá, sem tentar logar,
então serve para conferir a suposição com risco zero de bloquear a conta.

**Cenários do laço com dados falsos.** Oito situações que o bot precisa acertar:
vaga que abre, fecha e reabre; inscrição que dá choque de horário; atividade que
some do painel; inscrição feita à mão pela pessoa; site que aceita a inscrição mas
não confirma.

**Página real.** As 596 mil letras de uma resposta verdadeira do Even3, para
conferir que as 83 atividades são lidas, que nenhuma se perde no parser, que todos
os campos usados existem em todas elas e que os acentos sobrevivem.

**Servidor local imitando o Even3.** Um `http.server` de trinta linhas que devolve
a página, aceita a inscrição e renova cookie por `Set-Cookie`. Foi esse teste que
achou o bug do cookie duplicado descrito abaixo, nenhum teste com dados falsos
pegaria.

## Três bugs que valeram o projeto

**Booleanos que chegam como texto.** O `chompjs` devolve `true`/`false` como
string, e em Python qualquer texto não vazio é verdadeiro, inclusive `"false"`.
O bot achava que eu já estava inscrito em tudo e parava de monitorar na primeira
rodada. Só apareceu porque o modo de diagnóstico listava as inscrições e a lista
veio com o evento inteiro.

**Os 16 bytes.** O erro 500 do `realizarinscricao`, descrito acima. A resposta do
servidor era uma página de erro genérica, sem pista nenhuma. O que resolveu foi
comparar o `Content-Length` da minha requisição com o do navegador.

**Cookie duplicado.** Trocar o controle manual de cookies pelo `requests.Session`
introduziu um bug silencioso: cookies carregados do arquivo ficavam sem domínio, e
os renovados pelo servidor vinham com domínio. Para a biblioteca eram dois cookies
distintos de mesmo nome, e a sessão passou a enviar `AUTH=antigo; AUTH=novo` para
o site.

## Decisões de projeto

**O bot nunca cancela nada.** Existe uma função `cancelar`, usada só no modo de
teste. Inscrever é reversível; cancelar uma atividade lotada não é, porque a vaga
vai para outra pessoa no mesmo instante. Essa decisão fica com a pessoa.

**Ele confirma antes de comemorar.** Depois de inscrever, o bot relê a página e só
avisa "garantido" se `estouInscrito` virou verdadeiro. Uma versão anterior
confiava só na resposta do POST, e teria avisado sucesso, parado de monitorar e
perdido a vaga se o site aceitasse sem registrar.

**A espera entre logins não vale para o cookie.** O Even3 bloqueia a conta por
alguns minutos depois de tentativas seguidas de login, então o bot só tenta uma
vez a cada vinte minutos. Mas reler o arquivo de cookie é de graça e sem risco,
então isso acontece a cada volta: quando a sessão cai e você cola um cookie novo,
o bot volta a funcionar em um minuto, não em vinte.

**Intervalo com variação.** Um minuto mais um número aleatório de segundos, para
não bater no servidor sempre no mesmo instante.

## Limites conhecidos

- Se o Even3 mudar o nome do objeto embutido na página, o bot interpreta como
  sessão expirada em vez de detectar o problema.
- O login automático não passa por captcha. Hoje o fluxo do Even3 não usa um; se
  passar a usar, resta colar o cookie na mão.
- O filtro do `--listar` compara texto exato, então acentos contam.

## Sobre uso

Feito para uso pessoal, na minha própria conta, em um evento gratuito da minha
universidade. Publicado pouco antes do evento começar.

## Como foi construído

Desenvolvido em par com A IA, ela acelerou a escrita e ajudou alguns bugs que pra mim eram 
totalmente novos; as decisões de projeto, investigação de erros, a leitura do tráfego no DevTools e a validação
de cada hipótese contra dados reais foram minhas. Vale registrar que a parte mais
difícil, os três bugs acima, não saiu de sugestão de ferramenta, e sim de
comparar byte a byte o que o navegador mandava com o que o meu código mandava.

## Resultado

Rodou durante a 28ª Semana Integrada e fez 4 inscrições sozinho. A mais
relevante foram duas palestras esgotadas desde o primeiro dia: a vaga abriu
às 22h40 de uma terça e o bot inscreveu em menos de um minuto, sem
ninguém olhando para a tela.

O código publicado aqui é uma reescrita da versão que rodou no evento:
mesmo comportamento, reorganizado em um arquivo só, com os modos de
diagnóstico e a bateria de testes descrita acima.

## Tecnologias

Python 3, [requests](https://requests.readthedocs.io),
[chompjs](https://github.com/Nykakin/chompjs) e [ntfy.sh](https://ntfy.sh).

# Guia de uso

Para quem administra um servidor aaPanel e quer saber **qual site, URL, IP ou
erro está deixando o servidor lento**.

A saída da CLI é em inglês; as explicações aqui são em português.

Este guia cobre **somente o que já existe e funciona**. O que está planejado mas
não implementado está no fim, em [O que ainda não existe](#o-que-ainda-não-existe).

---

## Índice

1. [Em trinta segundos](#em-trinta-segundos)
2. [Instalação](#instalação)
3. [Primeiro comando: `doctor`](#primeiro-comando-doctor)
4. [Ligar o monitoramento](#ligar-o-monitoramento)
5. [O dia a dia](#o-dia-a-dia)
6. [Quando o servidor fica lento](#quando-o-servidor-fica-lento)
7. [Como ler um diagnóstico](#como-ler-um-diagnóstico)
8. [O que o aaDoctor não consegue ver](#o-que-o-aadoctor-não-consegue-ver)
9. [Configuração](#configuração)
10. [Códigos de saída](#códigos-de-saída)
11. [Problemas comuns](#problemas-comuns)
12. [Atualizar e remover](#atualizar-e-remover)
13. [O que ainda não existe](#o-que-ainda-não-existe)

---

## Em trinta segundos

```bash
sudo ./install.sh        # instala
aadoctor doctor          # confere o ambiente
aadoctor enable          # liga o daemon
```

Depois disso ele fica observando. Quando o servidor der um pico:

```bash
aadoctor incidents       # o que foi registrado
aadoctor diagnose        # o que a evidência diz sobre o último pico
```

É isso. O resto deste guia explica o que aparece na tela.

**O aaDoctor não muda nada no seu servidor.** Ele lê logs, lê `/proc` e escreve
só dentro dos diretórios dele. Não reinicia Nginx, não reinicia PHP, não bloqueia
IP, não mexe em permissão, não altera site.

---

## Instalação

Requisitos: Linux, Python 3.8+, systemd, aaPanel com Nginx. Sem pip, sem
virtualenv, sem Docker, sem banco.

```bash
sudo ./install.sh
```

O instalador é idempotente — rodar vinte vezes dá o mesmo resultado que rodar
uma. Ele **não sobrescreve** um `/etc/aadoctor/config.toml` existente, e
**não liga o serviço sozinho**.

Se você clonou o repositório e o script não executa:

```bash
sudo bash ./install.sh
```

Onde as coisas ficam:

```text
/opt/aadoctor/            o programa
/etc/aadoctor/config.toml a configuração, sua
/var/lib/aadoctor/        estado, incidentes
/var/log/aadoctor/        log interno do aaDoctor
/usr/local/bin/aadoctor   o comando
```

Nada é escrito em `/www/server/`, `/www/wwwroot/` ou `/www/wwwlogs/`.

---

## Primeiro comando: `doctor`

Sempre o primeiro. Confere o ambiente e **não altera nada**.

```bash
aadoctor doctor
```

```text
aaDoctor environment check

[OK] Linux detected
[OK] Python 3 detected
[OK] aaPanel detected
[OK] Nginx configuration directory detected
[OK] /www/wwwlogs readable

Sites found:       25
Access logs:       21 configured
Error logs:        21 configured

Warnings:
- cliente.com.br: no access log configured

Environment ready.
```

Cada checagem é `[OK]`, `[WARN]` ou `[FAIL]`. **Warning não esconde falha**: se
algo falhou, a última linha diz que o ambiente não está pronto.

Um site sem access log configurado continua sendo monitorado pelo error log. Ele
só não entra nas contas de tráfego.

---

## Ligar o monitoramento

```bash
sudo aadoctor enable
```

Equivale a `systemctl enable --now aadoctor`, e é idempotente.

```bash
aadoctor status
```

```text
aaDoctor 0.1.0-dev

Installed:
yes

aaPanel:
detected

Sites:
25

Daemon:
active (enabled)

Current load:
1.28

CPUs:
2

Load/core:
0.64

Last incident:
none
```

**Depois de ligar, espere.** O aaDoctor só começa a ler os logs a partir do
momento em que subiu — ele nunca lê histórico. Um log de 14 GB não é lido do
começo; ele começa no fim do arquivo. Então nos primeiros minutos não há o que
mostrar, e isso é o comportamento correto.

---

## O dia a dia

### `aadoctor sites`

O que ele descobriu no aaPanel:

```text
SITE              ACCESS   ERROR
example.com       yes      yes
cliente.com.br    none     missing
quiet.com         off      yes
```

Os quatro estados são diferentes de propósito:

| Estado | Significado |
|---|---|
| `yes` | configurado e o arquivo existe |
| `missing` | configurado, mas o arquivo não está lá |
| `none` | nada configurado no vhost |
| `off` | log desligado (`access_log off`) |
| `?` | caminho relativo, não resolvido |

`none` e `missing` são problemas diferentes e por isso têm nomes diferentes.

### `aadoctor top`

O que está acontecendo agora:

```bash
aadoctor top                 # últimos 5 minutos
aadoctor top --window 1m     # último minuto
aadoctor top --site loja.com.br
aadoctor top --json
```

Mostra sites, paths, IPs, classes de status e tipos de erro da janela.

Ele diz **o que aconteceu**. Ele nunca diz de quem é a culpa — isso é o
`diagnose`. Um relatório de medição que começasse a concluir seria a coisa mais
perigosa nesta ferramenta.

`top` também avisa quando a foto está velha, quando muitas linhas não foram
interpretadas, e quanto tráfego ficou de fora por limite de cardinalidade.

---

## Quando o servidor fica lento

### `aadoctor incidents`

```text
ID                    STARTED              DURATION  PEAK/CORE  SEVERITY
2026-09-23T04-12-06   2026-09-23 04:12:06      8m12s      21.80  critical
2026-09-23T03-40-51   2026-09-23 03:40:51  interrupted     4.15  high
2026-09-23T02-05-33   2026-09-23 02:05:33      2m05s       1.42  high
```

Um incidente é um período em que o load ficou alto. `--limit N` e `--json`.

Na coluna de duração:

- `open` — ainda está acontecendo;
- `interrupted` — o daemon foi reiniciado no meio e o fim é genuinamente
  desconhecido. Não é erro: é o registro se recusando a inventar um horário.

**Incidente não é diagnóstico.** Ele registra que o servidor estava sob carga e o
que os logs mostravam. Interpretar isso é o `diagnose`.

### `aadoctor show <id>`

O registro bruto de um incidente, como foi gravado — load no início e no pico, e
o tráfego congelado junto. Nunca recalculado.

### `aadoctor diagnose`

```bash
aadoctor diagnose                        # o incidente mais recente
aadoctor diagnose 2026-09-23T04-12-06    # um específico, por mais antigo que seja
aadoctor diagnose --json
```

O diagnóstico é **calculado na hora**, a partir do incidente gravado. Ele nunca
é escrito de volta. Isso significa que um incidente de semanas atrás continua
diagnosticável mesmo depois que os logs dele já rotacionaram — e é lido com as
regras de hoje, não com as da época.

---

## Como ler um diagnóstico

```text
aaDoctor Diagnosis

Incident:  2026-09-22T12-41-20  (CLOSED, critical)
Peak load: 9.82 over 4 CPUs - 2.45 per core
Window:    300s at the peak, 300s of data, 10,833 requests

PRIMARY SITE
loja.com.br

PRIMARY PATH
/wp-cron.php

ASSOCIATED IP
45.xxx.xxx.xxx
high request concentration from one address; it may be a CDN, proxy, NAT,
integration or crawler rather than an attack

EVIDENCE
- loja.com.br generated 82.3% of requests (8,922 of 10,833)
- /wp-cron.php generated 53.9% of loja.com.br's requests (4,812 of 8,922)
- 45.xxx.xxx.xxx generated 46.0% of loja.com.br's requests (4,102)
- 38 upstream timeout errors
- 17 responses were 5xx (502: 17)

FINDINGS
VERY HIGH  ONE_SITE_DOMINATING
HIGH       ONE_URL_DOMINATING
HIGH       UPSTREAM_TIMEOUT
MEDIUM     ONE_IP_DOMINATING

NOT EVALUABLE
TRAFFIC_SPIKE          insufficient previous window

CONFIDENCE
VERY HIGH

Evidence points to loja.com.br, with /wp-cron.php as the primary suspect.
```

Quatro coisas que valem entender:

**`CONFIDENCE` não é probabilidade.** `VERY HIGH` não quer dizer "95% de chance
de ter sido isto". É a força da evidência numa escala que este projeto define, e
nada além disso.

**`NOT EVALUABLE` é diferente de "não achou nada".** Uma regra que nunca teve
dado para rodar não é uma regra que rodou e não viu nada. Sem essa distinção,
silêncio vira calmaria — e não é.

**`PRIMARY PATH`, não "URL".** O que é agregado é o path, sem query string.

**`ASSOCIATED IP` é associado, não acusado.** O aaDoctor guarda `site → paths` e
`site → ips` separadamente, nunca `site × ip × path`. Ele pode dizer que o site
teve um path dominante e um IP dominante; ele **não** pode dizer que aquele IP
chamou aquele path, e não insinua isso.

E o caso em que ele não conclui:

```text
CONFIDENCE
-

No clear log-based cause identified. The load rise is recorded, but the
monitored Nginx and PHP logs show no dominant site, path, address or error
behind it.
```

Isso também é resultado útil. Ele está dizendo **onde não procurar**.

---

## O que o aaDoctor não consegue ver

Esta seção existe porque foi a lição mais cara do projeto.

Num servidor real — 2 GB de RAM, 2 CPUs, 25 sites — o aaDoctor registrou vinte
incidentes em doze horas, com load chegando a 43 por core. Todos os diagnósticos
voltaram inconclusivos. **E todos estavam certos**: o tráfego HTTP estava estável
ou caindo. A causa era pressão de memória com swap sustentado, e isso não aparece
em log de servidor web.

Então: **se o `diagnose` disser que não há causa visível nos logs, acredite nele
e olhe o sistema.** Comece por:

```bash
free -m            # memória e swap
vmstat 5 5         # colunas si/so: swap entrando e saindo
df -h ; df -i      # disco e inodes
```

Se `si` e `so` estiverem consistentemente acima de zero, a máquina está
paginando e o load não vem do HTTP.

O que hoje está fora do alcance dele:

```text
memória, swap, I/O, processos do sistema
backup, cron, tarefa de manutenção
banco de dados remoto
processo que não pertence a site nenhum
formato de log customizado (log_format próprio não é interpretado)
```

Isso está planejado — ver [O que ainda não existe](#o-que-ainda-não-existe).

Outras limitações que valem saber:

- **As contagens podem ficar levemente altas depois de um crash**, por desenho.
- **Os agregados não sobrevivem a um restart** do daemon.
- **Um path ou IP dominante num site que não é o mais movimentado não é
  reportado.** A alternativa — pegar a maior porcentagem em qualquer lugar —
  faria um site pequeno e parado ganhar sempre.
- **Traces PHP de várias linhas não são juntados.**
- **`include` em vhost não é seguido.**

---

## Configuração

`/etc/aadoctor/config.toml`. Um update **nunca** sobrescreve.

```toml
[monitor]
enabled = true
interval_seconds = 10

[discovery]
interval_seconds = 60

[load]
enabled = true
trigger_per_cpu = 1.00      # abre incidente
critical_per_cpu = 2.00     # marca como critical
recovery_per_cpu = 0.75     # fecha incidente
trigger_polls = 2
recovery_polls = 3

[logs]
window_seconds = 300

[incidents]
retention_days = 30
```

Abrir e fechar usam números diferentes de propósito: é isso que impede um
incidente de piscar quando o load fica oscilando em torno do limite.

Há uma seção `[rules]` com os limiares das regras determinísticas. Ela vem
**comentada** em `config.example.toml`: os valores embutidos são o que quase todo
servidor deve usar, e expor dezenas de botões antes de saber quais realmente
precisam de ajuste só deixa a ferramenta mais difícil. Os valores efetivamente
usados saem em `aadoctor diagnose --json`.

Depois de mudar qualquer coisa:

```bash
sudo systemctl restart aadoctor
```

---

## Códigos de saída

```text
0  sucesso
1  erro de uso ou comando desconhecido
2  ambiente não está pronto (doctor reprovou uma checagem)
3  objeto não encontrado (id de incidente desconhecido)
4  daemon não está rodando e o comando precisa dele
5  privilégio insuficiente (precisa de root)
```

**`diagnose` sai `0` independente do que concluir.** Um código diferente de zero
para "achei um incidente" seria útil num script e surpreendente num prompt — e a
leitura surpreendente é a que alguém tem às três da manhã.

---

## Problemas comuns

**`sudo ./install.sh: command not found`**
O bit de execução se perdeu no clone. Use `sudo bash ./install.sh`.

**`aadoctor top` diz que não há dados**
O daemon não está rodando, ou subiu há pouco. Confira com `aadoctor status`.
Lembre que ele começa a ler do fim dos logs.

**`aadoctor status` não mostra load nem incidente**
Essas linhas vêm do que o daemon publicou. Sem daemon rodando, elas não
aparecem — em vez de mostrar um número velho.

**Nenhum site aparece em `top`, mas `sites` lista todos**
Provavelmente um `log_format` customizado. O aaDoctor entende os formatos padrão
do Nginx (`common`, `combined` e o `main` que o aaPanel escreve) e não tenta
adivinhar além disso.

**Muitos incidentes**
Se o servidor realmente está em sofrimento, não são falso positivo — veja
[O que o aaDoctor não consegue ver](#o-que-o-aadoctor-não-consegue-ver). Se você
tem certeza de que não está, `trigger_per_cpu` é ajustável.

**Rodar sem root**
Comandos de leitura funcionam onde as permissões deixarem, e dizem claramente
quando uma permissão limitou o resultado. Ler os logs de todos os sites sem
alterar permissão nenhuma do aaPanel implica root, e essa foi uma escolha
deliberada: preferimos um processo root só de leitura a sair dando `chmod` em
arquivo do aaPanel.

---

## Atualizar e remover

```bash
sudo aadoctor update                  # última versão publicada
sudo aadoctor update --version 0.1.3
```

Preserva `/etc/aadoctor/` e `/var/lib/aadoctor/`, valida SHA256 antes de trocar
qualquer arquivo, e reinicia **somente** o próprio serviço.

```bash
sudo aadoctor disable
sudo aadoctor uninstall               # mantém config e dados
sudo aadoctor uninstall --purge       # remove tudo
```

```text
aaDoctor completely removed.

No aaPanel files were modified.
```

E isso é verdade: depois do purge não sobra cron, regra de firewall, permissão
alterada nem configuração mexida.

---

## O que ainda não existe

Está especificado e **não implementado**. Nada disto funciona hoje:

| Capacidade | Spec |
|---|---|
| Memória, swap, CPU, iowait, PSI, disco | SPEC-010 |
| Quais processos consumiram os recursos | SPEC-011 |
| Diagnóstico de causa de sistema, não só HTTP | SPEC-012 |
| Pools PHP-FPM e `pm.max_children` | SPEC-013 |
| Eventos de kernel e OOM | SPEC-014 |
| `doctor` dizendo o que ele consegue observar | SPEC-015 |
| Auditoria de segurança WordPress | SPEC-016 |
| Quarentena e restauração | SPEC-017 |
| Explicação por IA | SPEC-009 |

Detalhes em [docs/specs/README.md](specs/README.md). Um comando que não existe
**não está registrado**: `aadoctor explain` hoje é erro de uso listando o que
existe, e não um stub dizendo "não implementado". Nada aqui parece pronto sem
estar.

---

## Onde mais olhar

| Documento | Para quê |
|---|---|
| [README.md](../README.md) | visão, escopo, limites e garantias |
| [docs/specs/](specs/) | comportamento especificado |
| [docs/adr/](adr/) | decisões e o que elas custaram |
| [docs/DEVELOPMENT.md](DEVELOPMENT.md) | desenvolver e verificar |
| [CHANGELOG.md](../CHANGELOG.md) | o que mudou de fato |

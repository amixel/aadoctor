# aaDoctor

> Diagnóstico leve, determinístico e não invasivo para servidores **aaPanel + Nginx + PHP-FPM**.

O **aaDoctor** monitora servidores aaPanel para responder uma pergunta simples:

> **Qual site, URL, IP ou erro está fazendo o servidor ficar lento e derrubando os demais sites?**

O projeto foi criado para servidores que hospedam vários sites usando aaPanel, Nginx e PHP-FPM e nos quais um único site, bot, crawler, endpoint ou erro pode elevar o load e prejudicar todo o servidor.

O aaDoctor observa o servidor, acompanha os logs existentes e correlaciona picos de load com o que estava acontecendo nos sites naquele momento.

Ele é deliberadamente simples.

O aaDoctor:

* não substitui o aaPanel;
* não modifica o aaPanel;
* não modifica Nginx;
* não modifica PHP;
* não reinicia serviços;
* não bloqueia IPs automaticamente;
* não depende de banco de dados;
* não depende de Docker;
* não depende de Redis;
* não depende de IA para funcionar.

A inteligência principal do projeto é **determinística**.

IA é uma camada opcional de explicação.

---

# 1. Problema que queremos resolver

Em servidores compartilhando recursos entre dezenas de sites, é comum acontecer:

```text
load começa a subir
↓
sites ficam lentos
↓
PHP-FPM começa a acumular
↓
Nginx começa a retornar timeout / 502 / 504
↓
todos os sites parecem estar com problema
```

Quando o administrador entra no servidor, muitas vezes o pico já passou.

Executar:

```bash
top
```

ou:

```bash
htop
```

depois do incidente normalmente não responde:

```text
qual site causou isso?
qual URL foi responsável?
qual IP estava gerando carga?
foi bot?
foi crawler?
foi wp-cron?
foi admin-ajax?
foi alguma API?
houve milhares de 404?
houve FastCGI timeout?
houve erro PHP?
```

O aaDoctor existe para registrar e correlacionar essas evidências.

---

# 2. Objetivo principal

O objetivo do projeto é produzir diagnósticos como:

```text
INCIDENTE DETECTADO

Data:
22/09/2026 12:41:20

Servidor:
4 CPUs

Load:
9.82

Load por CPU:
2.45

Provável responsável:
loja.com.br

Motivo:
pico de requisições para /wp-cron.php

Requests do site:
8.922

Requests para /wp-cron.php:
4.812

Participação no tráfego:
54%

IP principal:
45.xxx.xxx.xxx

Requests do IP:
4.102

Também encontrados:
38 upstream timeouts
17 respostas HTTP 502

Confiança:
ALTA
```

O administrador precisa conseguir olhar esse relatório e saber **onde investigar primeiro**.

---

# 3. Filosofia do projeto

O aaDoctor segue alguns princípios que não devem ser quebrados sem uma decisão arquitetural explícita.

## 3.1. Observador, não administrador

O aaDoctor observa.

Ele não deve executar ações corretivas automaticamente.

Pode informar:

```text
IP 45.xxx.xxx.xxx realizou 4.102 requests em 3 minutos.
Possível flood ou crawler agressivo.
```

Mas não deve executar:

```bash
ufw deny ...
```

Pode informar:

```text
/wp-cron.php representa 72% das requests deste site.
```

Mas não deve alterar WordPress.

Pode informar:

```text
PHP/FastCGI apresentou 84 upstream timeouts.
```

Mas não deve reiniciar PHP-FPM.

---

# 4. Garantia não invasiva

## NON-INVASIVE GUARANTEE

O aaDoctor **MUST NOT**:

* modificar arquivos do aaPanel;
* modificar banco interno do aaPanel;
* modificar configuração do Nginx;
* modificar configuração do PHP;
* modificar pools PHP-FPM;
* modificar arquivos dos sites;
* modificar permissões dos sites;
* executar `chmod` em arquivos do aaPanel;
* executar `chown` em arquivos do aaPanel;
* executar `setfacl` em arquivos do aaPanel;
* criar cron dentro do aaPanel;
* modificar o crontab do usuário;
* modificar firewall;
* bloquear IPs;
* reiniciar Nginx;
* recarregar Nginx;
* reiniciar PHP-FPM;
* reiniciar MySQL;
* modificar MySQL;
* rotacionar logs do aaPanel;
* apagar logs do aaPanel;
* truncar logs do aaPanel;
* mover logs do aaPanel;
* alterar vhosts;
* alterar DNS;
* alterar aplicações hospedadas.

O aaDoctor pode apenas **ler** esses recursos.

---

# 5. O que o aaDoctor pode escrever

Todos os dados próprios do aaDoctor devem ficar isolados.

Instalação:

```text
/opt/aadoctor/
```

Configuração:

```text
/etc/aadoctor/
```

Estado:

```text
/var/lib/aadoctor/
```

Logs próprios:

```text
/var/log/aadoctor/
```

Comando global:

```text
/usr/local/bin/aadoctor
```

Service:

```text
/etc/systemd/system/aadoctor.service
```

Nenhum arquivo do aaDoctor deve ser gravado dentro de:

```text
/www/server/
/www/wwwroot/
/www/wwwlogs/
```

---

# 6. Escopo inicial

O MVP suporta:

```text
aaPanel
+
Nginx
+
PHP-FPM
+
Linux
```

O foco principal é análise de:

```text
access.log
error.log
loadavg
```

Podem ser utilizadas algumas métricas simples de `/proc` para contexto.

---

# 7. Fora do escopo inicial

Não fazem parte do MVP:

* painel web;
* Docker;
* Kubernetes;
* Redis;
* PostgreSQL;
* Elasticsearch;
* Loki;
* Prometheus;
* Grafana;
* agentes distribuídos;
* OpenTelemetry;
* tracing distribuído;
* controle remoto;
* auto-healing;
* firewall automático;
* bloqueio automático;
* tuning automático;
* gerenciamento de PHP;
* gerenciamento de Nginx;
* gerenciamento do aaPanel;
* monitoramento avançado de banco;
* SIEM;
* observabilidade enterprise.

O objetivo não é construir um Datadog.

O objetivo é descobrir:

> **quem está deixando meu servidor aaPanel lento?**

---

# 8. MySQL

MySQL não é uma dependência do aaDoctor.

Muitos servidores usam banco de dados remoto.

Por isso o monitoramento MySQL deve permanecer:

```toml
[mysql]
enabled = false
```

por padrão.

O aaDoctor deve funcionar integralmente sem MySQL local.

Uma integração futura pode ser adicionada como módulo opcional.

---

# 9. Inteligência Artificial

IA também não é requisito.

Configuração padrão:

```toml
[ai]
enabled = false
```

O detector determinístico deve continuar funcionando integralmente sem:

* API externa;
* chave de API;
* internet;
* modelo de linguagem.

A IA serve apenas para transformar um diagnóstico técnico estruturado em explicação humana.

Arquitetura:

```text
logs
↓
parser
↓
agregação
↓
regras determinísticas
↓
incident.json
↓
opcionalmente IA
↓
explicação
```

Nunca:

```text
logs gigantes
↓
IA
↓
"adivinhe o problema"
```

---

# 10. Modelo de IA pretendido

Quando habilitada, a integração deverá priorizar modelos rápidos e baratos.

Exemplo:

```text
GPT-5.6 Luna
```

O modelo recebe apenas um resumo estruturado.

Exemplo:

```json
{
  "load": 9.82,
  "cpus": 4,
  "site": "loja.com.br",
  "site_requests": 8922,
  "top_path": "/wp-cron.php",
  "top_path_requests": 4812,
  "top_ip": "45.xxx.xxx.xxx",
  "top_ip_requests": 4102,
  "upstream_timeouts": 38,
  "http_502": 17,
  "findings": [
    "ONE_SITE_DOMINATING",
    "ONE_URL_DOMINATING",
    "ONE_IP_DOMINATING",
    "UPSTREAM_TIMEOUT"
  ]
}
```

A IA não recebe o access log completo.

---

# 11. Estrutura do repositório

```text
aadoctor/
│
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CLAUDE.md
│
├── install.sh
├── uninstall.sh
│
├── aadoctor
├── config.example.toml
│
├── src/
│   └── aadoctor/
│       ├── __init__.py
│       ├── cli.py
│       ├── daemon.py
│       │
│       ├── discovery/
│       │   ├── __init__.py
│       │   └── aapanel.py
│       │
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── load.py
│       │   └── logs.py
│       │
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── nginx_access.py
│       │   └── nginx_error.py
│       │
│       ├── analyzers/
│       │   ├── __init__.py
│       │   ├── traffic.py
│       │   ├── errors.py
│       │   └── incidents.py
│       │
│       ├── rules/
│       │   ├── __init__.py
│       │   └── builtin.py
│       │
│       ├── ai/
│       │   ├── __init__.py
│       │   └── explainer.py
│       │
│       └── storage/
│           ├── __init__.py
│           └── json_store.py
│
├── systemd/
│   └── aadoctor.service
│
└── tests/
```

A estrutura deve permanecer pequena enquanto não existir necessidade real de expandi-la.

---

# 12. Estrutura instalada no servidor

```text
/opt/aadoctor/
├── src/
├── aadoctor
├── VERSION
└── LICENSE
```

Config:

```text
/etc/aadoctor/
└── config.toml
```

Estado:

```text
/var/lib/aadoctor/
├── state.json
├── offsets/
└── incidents/
```

Logs internos:

```text
/var/log/aadoctor/
└── aadoctor.log
```

CLI:

```text
/usr/local/bin/aadoctor
```

Service:

```text
/etc/systemd/system/aadoctor.service
```

---

# 13. Dependências

A primeira versão deve usar preferencialmente:

```text
Python 3
+
Python Standard Library
```

Evitar inicialmente:

```text
pip
virtualenv
poetry
pipenv
Node.js
npm
Docker
Redis
SQLite
```

Quanto menor a superfície de instalação, melhor.

---

# 14. Configuração

Arquivo:

```text
/etc/aadoctor/config.toml
```

Exemplo:

```toml
[monitor]
enabled = true
interval_seconds = 10

[discovery]
interval_seconds = 60

[load]
enabled = true
trigger_per_cpu = 1.00
critical_per_cpu = 2.00

[logs]
window_seconds = 300

[incidents]
retention_days = 30

[mysql]
enabled = false

[ai]
enabled = false
model = "gpt-5.6-luna"
```

Os valores ainda podem mudar durante o desenvolvimento.

---

# 15. Descoberta do aaPanel

O aaDoctor deve detectar automaticamente uma instalação aaPanel.

Primeiro caminho esperado:

```text
/www/server/panel/
```

Configurações Nginx esperadas:

```text
/www/server/panel/vhost/nginx/
```

Logs normalmente encontrados em:

```text
/www/wwwlogs/
```

O aaDoctor não deve depender exclusivamente do nome do arquivo.

Sempre que possível, deve ler os vhosts para descobrir:

```nginx
server_name example.com www.example.com;

access_log /www/wwwlogs/example.com.log;

error_log /www/wwwlogs/example.com.error.log;
```

Isso permite mapear:

```text
site
→
access log
→
error log
```

---

# 16. Discovery automático

Ao iniciar:

```text
aadoctor
↓
detecta aaPanel
↓
detecta vhosts
↓
extrai server_name
↓
extrai access_log
↓
extrai error_log
↓
começa monitoramento
```

O discovery deve ser executado novamente periodicamente.

Exemplo:

```text
a cada 60 segundos
```

Assim, criar um novo site no aaPanel não exige restart do aaDoctor.

---

# 17. Primeiro comportamento ao encontrar logs

O aaDoctor **não deve ler logs históricos inteiros** automaticamente.

Se encontrar:

```text
/www/wwwlogs/example.com.log
```

com:

```text
14 GB
```

não deve fazer:

```python
file.read()
```

nem começar na posição zero.

Na primeira observação:

```text
offset inicial = final do arquivo
```

Depois disso acompanha apenas conteúdo novo.

---

# 18. Offset dos logs

O aaDoctor mantém para cada arquivo:

```text
path
inode
offset
```

Exemplo:

```json
{
  "/www/wwwlogs/site1.com.log": {
    "inode": 1192281,
    "offset": 84477219
  },
  "/www/wwwlogs/site2.com.log": {
    "inode": 1192298,
    "offset": 1128829
  }
}
```

Estado armazenado em:

```text
/var/lib/aadoctor/state.json
```

---

# 19. Log rotation

O monitor deve suportar rotação de logs.

Se:

```text
inode antigo != inode atual
```

o aaDoctor entende que ocorreu rotação.

Exemplo:

```text
site.com.log
site.com.log.1
```

O aaDoctor passa a acompanhar o novo arquivo.

Não deve modificar o processo de rotação existente.

---

# 20. Monitor de load

O aaDoctor observa:

```text
/proc/loadavg
```

Obtendo:

```text
load1
load5
load15
```

Também descobre:

```text
CPU count
```

O valor principal de análise é:

```text
load_per_cpu = load1 / cpu_count
```

Exemplo:

```text
4 CPUs
load1 = 8

8 / 4 = 2.0
```

---

# 21. Load não é diagnóstico

Load alto não significa automaticamente CPU saturada.

O aaDoctor usa load apenas como:

> gatilho temporal para procurar o que estava acontecendo nos logs.

Exemplo:

```text
12:40 load 1.2
12:41 load 1.8
12:42 load 7.9
12:43 load 11.4
```

O sistema então pergunta:

```text
o que aconteceu nos logs entre aproximadamente
12:39 e 12:43?
```

---

# 22. Janelas de análise

O daemon pode manter janelas móveis.

Inicialmente:

```text
10 segundos
1 minuto
5 minutos
```

A janela principal de incidente pode ser:

```text
5 minutos
```

Exemplo:

```text
incidente:
12:42:00

janela analisada:
12:37:00 → 12:42:00
```

---

# 23. Informações extraídas do access log

Sempre que o formato permitir:

```text
timestamp
IP
method
path
query string
status
bytes
referer
user agent
request time
upstream response time
```

Nem todos os campos estarão disponíveis.

O parser deve funcionar com ausência de campos opcionais.

---

# 24. Métricas agregadas

Por janela:

```text
requests totais
requests por site
requests por IP
requests por path
requests por status
requests por método
requests por user-agent
```

Quando disponível:

```text
request_time médio
request_time máximo
request_time acumulado
upstream_response_time
```

---

# 25. Site dominante

Queremos descobrir situações como:

```text
Servidor:
23.000 requests em 5 minutos

loja.com.br:
18.400 requests
```

Participação:

```text
80%
```

Finding:

```text
ONE_SITE_DOMINATING
```

---

# 26. URL dominante

Exemplo:

```text
loja.com.br

/wp-cron.php                 8.812
/wp-admin/admin-ajax.php     2.212
/                              822
/produto/123                   182
```

Se uma URL domina o volume:

```text
ONE_URL_DOMINATING
```

---

# 27. IP dominante

Exemplo:

```text
45.xxx.xxx.xxx   9.102 requests
```

Se um único IP representa parcela anormal do tráfego:

```text
ONE_IP_DOMINATING
```

Isso não significa automaticamente ataque.

O relatório deve dizer:

```text
possível bot, crawler, integração ou flood
```

e apresentar evidências.

---

# 28. User-Agent

O sistema deve agregar User-Agent para ajudar a distinguir:

```text
Googlebot
Bingbot
curl
python-requests
browser
crawler desconhecido
```

Não é necessário manter catálogo complexo inicialmente.

---

# 29. Status HTTP

Contar:

```text
2xx
3xx
4xx
5xx
```

Com destaque para:

```text
403
404
408
429
499
500
502
503
504
```

---

# 30. Error log

O parser deve reconhecer inicialmente padrões conhecidos.

## Upstream

```text
upstream timed out
```

Finding:

```text
UPSTREAM_TIMEOUT
```

---

## FastCGI

Padrões relacionados a:

```text
FastCGI sent in stderr
connect() failed
upstream prematurely closed connection
recv() failed
```

Finding:

```text
FASTCGI_ERROR
```

---

## Nginx

Exemplos:

```text
worker_connections are not enough
too many open files
```

Finding apropriado conforme o caso.

---

# 31. Erros PHP

Quando presentes no error log:

```text
PHP Fatal error
PHP Warning
PHP Parse error
Allowed memory size exhausted
Maximum execution time exceeded
```

Podem gerar:

```text
PHP_FATAL_ERROR
PHP_MEMORY_EXHAUSTED
PHP_EXECUTION_TIMEOUT
PHP_ERROR_SPIKE
```

---

# 32. Findings iniciais

A primeira versão deve priorizar poucas regras boas.

Inicialmente:

```text
TRAFFIC_SPIKE

ONE_SITE_DOMINATING

ONE_URL_DOMINATING

ONE_IP_DOMINATING

BOT_OR_CRAWLER_SPIKE

NOT_FOUND_FLOOD

HTTP_5XX_SPIKE

UPSTREAM_TIMEOUT

FASTCGI_ERROR

PHP_ERROR_SPIKE

PHP_MEMORY_EXHAUSTED

PHP_EXECUTION_TIMEOUT
```

Não criar dezenas de findings antes de termos casos reais.

---

# 33. Combinação de findings

O valor principal do aaDoctor vem da correlação.

Exemplo:

```text
TRAFFIC_SPIKE
+
ONE_SITE_DOMINATING
+
ONE_URL_DOMINATING
+
ONE_IP_DOMINATING
```

Pode produzir:

```text
Um único IP está gerando grande quantidade de requests
para uma única URL de um único site e coincide com o pico
de load do servidor.
```

Outro exemplo:

```text
ONE_SITE_DOMINATING
+
UPSTREAM_TIMEOUT
+
HTTP_5XX_SPIKE
```

Pode produzir:

```text
Um site concentra a maioria da atividade e apresenta
timeouts de upstream e aumento de erros 5xx.
```

---

# 34. Confiança

A confiança é calculada por regras determinísticas.

Exemplo:

```text
LOW
MEDIUM
HIGH
VERY_HIGH
```

Ou internamente:

```text
0.00 → 1.00
```

A IA não define confiança.

---

# 35. Evidence-first

Todo diagnóstico precisa apresentar evidências.

Nunca:

```text
Causa: WordPress.
```

Preferir:

```text
Provável responsável:
loja.com.br

Evidências:

- 82% das requests do servidor;
- 4.812 requests para /wp-cron.php;
- 78% dessas requests vieram do mesmo IP;
- 38 upstream timeouts;
- pico coincidente com load/core de 2.45.
```

---

# 36. Incidentes

Um incidente é criado quando regras relevantes coincidem com degradação do servidor.

Exemplo:

```text
/var/lib/aadoctor/incidents/
└── 2026-09-22T12-41-20.json
```

---

# 37. Formato de incidente

Exemplo:

```json
{
  "id": "2026-09-22T12-41-20",
  "started_at": "2026-09-22T12:41:20-03:00",

  "system": {
    "cpu_count": 4,
    "load1": 9.82,
    "load5": 6.13,
    "load15": 3.11,
    "load_per_cpu": 2.45
  },

  "traffic": {
    "total_requests": 10833,
    "window_seconds": 300
  },

  "suspect": {
    "site": "loja.com.br",
    "requests": 8922,
    "share": 0.823
  },

  "top_path": {
    "path": "/wp-cron.php",
    "requests": 4812
  },

  "top_ip": {
    "ip": "45.xxx.xxx.xxx",
    "requests": 4102
  },

  "errors": {
    "upstream_timeout": 38,
    "http_502": 17
  },

  "findings": [
    {
      "code": "ONE_SITE_DOMINATING",
      "confidence": 0.98
    },
    {
      "code": "ONE_URL_DOMINATING",
      "confidence": 0.95
    },
    {
      "code": "ONE_IP_DOMINATING",
      "confidence": 0.91
    }
  ]
}
```

O schema poderá evoluir antes da primeira versão estável.

---

# 38. CLI

O aaDoctor deve ser principalmente uma aplicação CLI.

Comandos pretendidos:

```bash
aadoctor status
```

```bash
aadoctor doctor
```

```bash
aadoctor enable
```

```bash
aadoctor disable
```

```bash
aadoctor top
```

```bash
aadoctor diagnose
```

```bash
aadoctor incidents
```

```bash
aadoctor show <incident>
```

```bash
aadoctor explain <incident>
```

```bash
aadoctor update
```

```bash
aadoctor uninstall
```

---

# 39. `aadoctor doctor`

Valida o ambiente.

Exemplo:

```text
aaDoctor environment check

[OK] Linux detected
[OK] Python 3 detected
[OK] aaPanel detected
[OK] Nginx configuration directory detected
[OK] /www/wwwlogs readable

Sites found:       48
Access logs found: 47
Error logs found:  48

[WARN]
cliente.com.br has no access log configured

Ready to monitor.
```

`doctor` não modifica nada.

---

# 40. `aadoctor status`

Exemplo:

```text
aaDoctor 0.1.0

aaPanel:
detected

Sites:
48

Access logs:
47

Error logs:
48

Daemon:
running

Monitoring:
enabled

Current load:
1.28

CPUs:
4

Load/core:
0.32

Last incident:
none
```

---

# 41. `aadoctor top`

Exemplo:

```text
LAST 5 MINUTES

SITE                       REQUESTS
loja.com.br                   8.922
cliente.com.br                1.822
blog.com.br                     744

TOP PATH

loja.com.br/wp-cron.php       4.812

TOP IP

45.xxx.xxx.xxx                4.102

ERRORS

502                               8
504                               3
upstream timeout                  5
```

---

# 42. `aadoctor diagnose`

Exemplo:

```text
SERVER DEGRADATION DETECTED

22/09/2026 12:41:20

Load:
9.82

CPUs:
4

Load/core:
2.45


PROBABLE RESPONSIBLE SITE

loja.com.br


PRIMARY FINDING

Excessive requests to:

/wp-cron.php


EVIDENCE

4.812 requests / 5 min

16.0 requests/sec average

54% of all server requests

Top IP:
45.xxx.xxx.xxx

Requests from IP:
4.102

Additional evidence:

38 upstream timeouts
17 HTTP 502 responses


CONFIDENCE

HIGH
```

---

# 43. `aadoctor enable`

Executa conceitualmente:

```bash
systemctl enable --now aadoctor
```

Deve ser idempotente.

Executar várias vezes produz o mesmo estado final.

---

# 44. `aadoctor disable`

Executa conceitualmente:

```bash
systemctl disable --now aadoctor
```

Depois disso:

```text
nenhum daemon do aaDoctor deve permanecer executando
```

Os dados podem permanecer no servidor.

---

# 45. Instalação

Objetivo de UX:

```bash
curl -fsSL https://aadoctor.dev/install.sh | bash
```

Depois:

```bash
aadoctor doctor
```

E:

```bash
aadoctor enable
```

Três comandos.

---

# 46. Instalação via GitHub

Enquanto não existir domínio próprio:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/aadoctor/main/install.sh | bash
```

O `install.sh` deve preferencialmente baixar uma release publicada em vez de clonar `main`.

---

# 47. Não usar `git clone` como requisito

O servidor não precisa ter Git instalado.

O instalador deve poder trabalhar com:

```text
curl
tar
sha256sum
python3
systemd
```

---

# 48. Releases

Releases versionadas:

```text
v0.1.0
v0.1.1
v0.2.0
```

Artefato:

```text
aadoctor-0.1.0.tar.gz
```

Checksum:

```text
aadoctor-0.1.0.tar.gz.sha256
```

O instalador deve validar SHA256 antes da instalação.

---

# 49. Instalação idempotente

Executar:

```bash
install.sh
```

uma vez ou vinte vezes deve resultar no mesmo estado.

O instalador:

1. verifica privilégios;
2. verifica sistema;
3. verifica Python;
4. detecta aaPanel;
5. cria diretórios necessários;
6. baixa versão;
7. verifica checksum;
8. instala arquivos;
9. preserva configuração existente;
10. instala ou atualiza service;
11. executa `systemctl daemon-reload`.

---

# 50. Preservação da configuração

Se existir:

```text
/etc/aadoctor/config.toml
```

uma atualização normal **não deve sobrescrevê-lo**.

A configuração padrão é copiada apenas quando ainda não existe configuração.

---

# 51. Atualização

Comando pretendido:

```bash
aadoctor update
```

O processo:

```text
descobre versão atual
↓
descobre versão disponível
↓
baixa release
↓
valida SHA256
↓
substitui /opt/aadoctor
↓
preserva /etc/aadoctor
↓
preserva /var/lib/aadoctor
↓
reinicia somente o próprio aaDoctor se necessário
```

Nunca reiniciar Nginx ou PHP.

---

# 52. Rollback futuro

Não é obrigatório no MVP.

Mas a estrutura deve permitir futuramente:

```bash
aadoctor update --version 0.1.3
```

ou rollback para versão anterior.

---

# 53. Desinstalação

Comando:

```bash
aadoctor uninstall
```

Deve:

```text
parar daemon
desabilitar service
remover service
remover CLI
remover /opt/aadoctor
executar systemctl daemon-reload
```

Por padrão pode preservar:

```text
/etc/aadoctor/
/var/lib/aadoctor/
```

---

# 54. Purge

Para remoção completa:

```bash
aadoctor uninstall --purge
```

Remove:

```text
/opt/aadoctor
/etc/aadoctor
/var/lib/aadoctor
/var/log/aadoctor
/etc/systemd/system/aadoctor.service
/usr/local/bin/aadoctor
```

Resultado esperado:

```text
aaDoctor completely removed.

No aaPanel files were modified.
```

---

# 55. Zero vestígios no aaPanel

Após `uninstall --purge`, não deve existir nenhuma alteração deixada em:

```text
/www/server/panel/
/www/server/panel/vhost/
/www/wwwlogs/
/www/wwwroot/
```

Também não devem existir:

```text
cron entries
firewall rules
modified permissions
modified ownership
modified Nginx configs
modified PHP configs
```

---

# 56. systemd

O daemon deve utilizar systemd.

Exemplo inicial:

```ini
[Unit]
Description=aaDoctor aaPanel Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/aadoctor/aadoctor daemon
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Isso ainda poderá ser endurecido conforme testes.

---

# 57. Privilégios

Inicialmente pode ser necessário executar como root para conseguir ler diferentes logs de sites sem modificar permissões existentes.

Preferimos:

```text
processo root read-only
```

a executar alterações como:

```text
chmod
chown
setfacl
```

nos arquivos do aaPanel.

No futuro podemos avaliar usuário dedicado caso isso possa ser feito sem interferir no ambiente existente.

---

# 58. Performance

O próprio monitor não pode virar causa de load.

Objetivo:

```text
baixo uso de CPU
baixo uso de RAM
I/O mínimo
```

Princípios:

* nunca reler arquivos completos;
* ler somente bytes novos;
* agregar dados em memória;
* limitar cardinalidade;
* não guardar cada request indefinidamente;
* não copiar access logs;
* não indexar logs inteiros;
* não executar regex excessivamente caras;
* não executar subprocessos em loop quando Python puder ler `/proc`;
* não chamar IA automaticamente em cada incidente.

---

# 59. Cardinalidade

Um ataque pode produzir milhões de URLs únicas.

Por isso devemos limitar estruturas como:

```text
top IPs
top paths
top user-agents
```

A implementação deve impedir crescimento de memória sem limite.

Exemplo futuro:

```text
top 1000 paths por janela
top 1000 IPs por janela
```

O valor exato será definido através de testes.

---

# 60. Query strings

Por padrão devemos avaliar normalização de URL.

Exemplo:

```text
/produto?id=1
/produto?id=2
/produto?id=3
```

Podem representar o mesmo endpoint:

```text
/produto
```

O sistema deve manter possibilidade de:

```text
path normalizado
+
query original quando necessário
```

para evitar cardinalidade desnecessária.

---

# 61. Dados sensíveis

O aaDoctor não deve guardar desnecessariamente:

* cookies;
* headers completos;
* bodies;
* POST bodies;
* tokens;
* Authorization headers;
* senhas;
* sessões.

O foco é diagnóstico de tráfego, não captura de conteúdo.

---

# 62. IPs

IPs podem ser armazenados nos incidentes locais porque fazem parte da análise operacional.

Quando enviados para IA, deve existir possibilidade futura de anonimização.

Por exemplo:

```text
177.10.20.30
```

pode virar:

```text
IP_1
```

antes do envio externo.

---

# 63. Retenção

Incidentes podem ter retenção configurável.

Exemplo:

```toml
[incidents]
retention_days = 30
```

O aaDoctor pode apagar **seus próprios arquivos antigos**.

Nunca logs do aaPanel.

---

# 64. Logs internos do aaDoctor

Arquivo:

```text
/var/log/aadoctor/aadoctor.log
```

Registrar apenas eventos operacionais importantes.

Exemplos:

```text
daemon started
site discovered
log rotation detected
incident created
configuration error
permission denied
AI request failed
```

Evitar log excessivamente verboso por padrão.

---

# 65. Falhas devem ser isoladas

Um site com log ilegível não pode derrubar o daemon.

Exemplo:

```text
48 sites encontrados

47 monitorados

1 ignorado:
permission denied
```

O restante continua funcionando.

---

# 66. Erros de parser

Uma linha desconhecida de log não deve derrubar o monitor.

Deve:

```text
ignorar linha
incrementar parser_error_count
continuar
```

---

# 67. Formatos de access log

O aaDoctor precisa funcionar com o formato comum do Nginx usado pelo aaPanel.

Campos adicionais são opcionais.

No futuro podemos recomendar um formato enriquecido contendo:

```nginx
$request_time
$upstream_response_time
$upstream_connect_time
$upstream_header_time
```

Mas o aaDoctor não deve alterar essa configuração automaticamente.

---

# 68. Enhanced logging futuro

Podemos documentar opcionalmente como o administrador pode adicionar tempos ao access log.

Isso deve ser:

```text
manual
opt-in
documentado
reversível
```

Nunca aplicado automaticamente pelo aaDoctor.

---

# 69. Request time

Quando `$request_time` existir, podemos calcular:

```text
request count
average request time
max request time
total request time
```

Uma métrica interessante é:

```text
total_cost ≈ requests × average_request_time
```

Exemplo:

```text
/                 20.000 × 0.04 s = 800 s
/admin/export        200 × 9.80 s = 1.960 s
```

Mesmo com poucas chamadas, `/admin/export` pode ter impacto maior.

---

# 70. Baselines

Baseline histórico não é requisito inicial.

A primeira versão deve trabalhar com regras simples.

Depois podemos aprender:

```text
requests normais por site
RPS normal
distribuição por horário
erros normais
```

Não adicionar sistema complexo de machine learning para isso.

---

# 71. Determinismo primeiro

Uma regra deve ser compreensível.

Exemplo conceitual:

```text
IF
site_requests / total_requests > 0.70

THEN
ONE_SITE_DOMINATING
```

Outro:

```text
IF
top_ip_requests / site_requests > 0.60

THEN
ONE_IP_DOMINATING
```

Os thresholds serão refinados com testes reais.

---

# 72. IA nunca deve criar fatos

Prompt da IA deve conter instrução equivalente a:

```text
Use somente as evidências fornecidas.

Não invente métricas.

Não invente causas.

Diferencie causa observada de hipótese.

Quando houver incerteza, informe.
```

---

# 73. Falha da IA

Se:

```text
API offline
timeout
quota esgotada
API key inválida
```

o incidente determinístico continua funcionando normalmente.

Exemplo:

```text
AI explanation unavailable.

Deterministic diagnosis remains available.
```

---

# 74. Segurança da API key

Se integração OpenAI for adicionada:

```text
API key
```

não deve ficar:

```text
incident.json
aadoctor.log
stdout
```

A configuração deverá prever variável de ambiente ou arquivo protegido.

---

# 75. Desenvolvimento local

Objetivo inicial:

```bash
python3 ./aadoctor doctor
```

e:

```bash
python3 ./aadoctor diagnose
```

Durante desenvolvimento poderão existir fixtures de logs em:

```text
tests/fixtures/
```

para reproduzir incidentes sem aaPanel real.

---

# 76. Fixtures

Casos importantes:

```text
normal traffic

one site dominating

one IP flood

one URL flood

404 flood

502 spike

504 spike

upstream timeout

PHP fatal errors

log rotation

truncated log

missing access log

missing error log

malformed line

huge query strings
```

---

# 77. Testes

Não queremos overengineering.

Priorizar testes para partes que podem causar problema real:

```text
parser
offset
log rotation
incident rules
installation safety
uninstallation
```

Especialmente:

> nenhum teste deve depender de alterar aaPanel real.

---

# 78. Instalação segura

`install.sh` deve usar:

```bash
set -euo pipefail
```

e validar cada etapa antes de substituir arquivos.

Não deve deixar instalação parcial silenciosa.

---

# 79. Update atômico

Quando possível:

```text
download temporário
↓
verify
↓
extract temporário
↓
replace
```

Evitar apagar instalação atual antes de validar novo pacote.

---

# 80. Comportamento em servidores sem aaPanel

`aadoctor doctor`:

```text
aaPanel not detected.

Expected:
 /www/server/panel/

No changes were made.
```

Por padrão a instalação pode abortar antes de ativar o daemon.

---

# 81. Servidores sem Nginx

Se aaPanel estiver usando Apache:

```text
Nginx not detected.

aaDoctor currently supports aaPanel + Nginx only.

No monitoring started.
```

Não tentar adaptar silenciosamente.

---

# 82. Princípio de compatibilidade

Primeiro:

```text
aaPanel + Nginx
```

Depois, se houver necessidade real:

```text
Apache
OpenLiteSpeed
LEMP genérico
```

Não antecipar abstrações desnecessárias.

---

# 83. Roadmap inicial

## Phase 1 — Foundation

* estrutura do projeto;
* CLI mínima;
* config;
* detecção do aaPanel;
* discovery de sites;
* systemd;
* install/uninstall.

---

## Phase 2 — Log Tail

* offsets;
* inode;
* rotação;
* leitura incremental;
* parser access log;
* parser error log.

---

## Phase 3 — Aggregation

* requests por site;
* requests por IP;
* requests por path;
* status HTTP;
* erros Nginx/PHP;
* janelas móveis.

---

## Phase 4 — Load Correlation

* `/proc/loadavg`;
* CPU count;
* load/core;
* criação de incidente;
* janela temporal.

---

## Phase 5 — Deterministic Findings

Implementar inicialmente:

```text
TRAFFIC_SPIKE
ONE_SITE_DOMINATING
ONE_URL_DOMINATING
ONE_IP_DOMINATING
NOT_FOUND_FLOOD
HTTP_5XX_SPIKE
UPSTREAM_TIMEOUT
FASTCGI_ERROR
PHP_ERROR_SPIKE
```

---

## Phase 6 — CLI Reports

```text
status
top
diagnose
incidents
show
```

---

## Phase 7 — AI Explanation

Somente depois do motor determinístico estar funcionando.

Implementar:

```bash
aadoctor explain <incident>
```

---

# 84. Especificações previstas

Documentos futuros:

```text
docs/specs/
```

Sugestão:

```text
SPEC-001-INSTALLATION-LIFECYCLE.md

SPEC-002-AAPANEL-DISCOVERY.md

SPEC-003-INCREMENTAL-LOG-MONITORING.md

SPEC-004-NGINX-LOG-PARSING.md

SPEC-005-TRAFFIC-AGGREGATION.md

SPEC-006-LOAD-INCIDENT-DETECTION.md

SPEC-007-DETERMINISTIC-RULES.md

SPEC-008-CLI-REPORTING.md

SPEC-009-AI-EXPLAINER.md
```

---

# 85. Regras para desenvolvimento com agentes de IA

Antes de implementar qualquer feature, o agente deve verificar este README.

Não adicionar:

```text
framework
database
service
dependency
abstraction
daemon adicional
API externa
```

sem necessidade concreta.

Quando uma feature puder ser implementada de forma pequena e direta, preferir essa forma.

---

# 86. Não overengineer

Evitar:

```text
repository pattern
event bus
dependency injection framework
CQRS
microservices
plugin architecture precoce
distributed queue
ORM
complex domain model
```

O aaDoctor é uma ferramenta de servidor pequena.

---

# 87. Código

Código:

```text
English
```

CLI e documentação inicialmente:

```text
English ou PT-BR conforme evolução
```

Comentários somente quando agregarem informação.

---

# 88. Compatibilidade Python

Definir posteriormente a versão mínima após verificar versões comuns nos servidores aaPanel.

Preferência:

```text
Python 3 disponível no servidor
```

Evitar exigir versão extremamente recente sem necessidade.

---

# 89. Fonte da verdade

Este `README.md` é a principal fonte de verdade do projeto.

Quando houver conflito entre:

```text
implementação
prompt antigo
comentário
issue
documento antigo
```

e este README, deve-se primeiro verificar se o README foi atualizado para refletir a decisão mais recente.

Decisões arquiteturais relevantes podem posteriormente ser extraídas para ADRs.

---

# 90. Regra para mudança de escopo

Qualquer mudança que transforme o aaDoctor de:

```text
observador
```

para:

```text
executor automático
```

deve ser considerada uma mudança arquitetural importante.

Exemplos:

```text
bloquear IP
restart PHP
alterar Nginx
alterar firewall
alterar WordPress
```

não devem entrar como pequenas features.

---

# 91. Critério de sucesso do MVP

O MVP estará cumprindo seu objetivo quando conseguirmos instalar em um servidor aaPanel e, durante um pico real, obter algo próximo de:

```text
Load elevado detectado às 03:17.

O site example.com respondeu por 79% das requests
durante os cinco minutos anteriores ao pico.

A URL /wp-admin/admin-ajax.php respondeu por 63%
das requests desse site.

O IP 192.x.x.x realizou 71% dessas chamadas.

Também foram encontrados:

42 upstream timeouts
18 respostas HTTP 502

Esse conjunto de evidências coincide temporalmente
com o aumento do load.
```

Isso já vale mais do que dezenas de métricas sem contexto.

---

# 92. O que não é necessário para considerar o projeto útil

Não precisamos inicialmente de:

```text
gráficos
dashboard
login
multi-user
mobile
SaaS
cloud
billing
agent orchestration
machine learning
```

Se a CLI descobrir corretamente a causa dos incidentes, o projeto já resolve o problema principal.

---

# 93. UX desejada

Instalação:

```bash
curl -fsSL https://aadoctor.dev/install.sh | bash
```

Verificação:

```bash
aadoctor doctor
```

Ativação:

```bash
aadoctor enable
```

Uso:

```bash
aadoctor status
```

```bash
aadoctor top
```

```bash
aadoctor diagnose
```

Desativação:

```bash
aadoctor disable
```

Remoção:

```bash
aadoctor uninstall --purge
```

É isso.

---

# 94. Resumo arquitetural

```text
                    aaPanel
                       │
                       │ READ ONLY
                       ▼
             Nginx vhost configs
                       │
                       ▼
              discovery de sites
                       │
           ┌───────────┴───────────┐
           ▼                       ▼
      access logs              error logs
           │                       │
           └───────────┬───────────┘
                       ▼
                incremental tail
                       │
                       ▼
                    parser
                       │
                       ▼
                  aggregation
                       │
                       │
/proc/loadavg ─────────┤
                       ▼
                correlation engine
                       │
                       ▼
              deterministic rules
                       │
                       ▼
                 incident.json
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
       CLI report             optional AI
                                  │
                                  ▼
                         human explanation
```

---

# 95. Princípio final

O aaDoctor deve permanecer:

```text
simples
leve
auditável
determinístico
reversível
idempotente
não invasivo
```

A prioridade não é quantidade de features.

A prioridade é responder com confiança:

> **o que estava acontecendo no servidor exatamente quando ele ficou lento?**

E principalmente:

> **qual site, URL, IP ou erro é o principal suspeito?**

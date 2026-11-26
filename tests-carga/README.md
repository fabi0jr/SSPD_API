# Teste de desempenho (k6)

O script [leituras.js](leituras.js) mede o `POST /leituras`, a rota que recebe os dados dos sensores. Cada usuário virtual do k6 faz o papel de um sensor. Ele envia uma leitura a cada `INTERVALO` segundos para o seu paciente, com a API key, do mesmo jeito que o simulador. Os valores ficam sempre dentro das faixas da API, para não gerar 422. Cada leitura tem um `id` novo, e cerca de 2% delas têm SpO₂ baixa, o que dá trabalho também ao worker.

Não é preciso instalar o k6: o teste roda com a imagem `grafana/k6:2.3.0` no Docker.

## Como rodar

Dentro de `tests-carga/`, com a API no ar:

```bash
mkdir -p resultados
docker run --rm --network host -u "$(id -u)" -v "$PWD":/carga -w /carga \
  -e API_KEY=chave-local -e SENSORES=10 -e N_PACIENTES=10 \
  grafana/k6:2.3.0 run -q leituras.js
```

Os três cenários do relatório, em sequência:

```bash
for n in 10 100 500; do
  docker run --rm --network host -u "$(id -u)" -v "$PWD":/carga -w /carga \
    -e BASE_URL=https://dominio -e API_KEY="$SENSOR_API_KEY" \
    -e SENSORES=$n -e N_PACIENTES=$n \
    grafana/k6:2.3.0 run -q leituras.js
  sleep 30   # deixa o worker esvaziar o backlog antes do próximo cenário
done
```

- `--network host` permite usar `localhost`. Com o Docker Compose na mesma máquina, use `BASE_URL=http://localhost` (o Nginx, na porta `HTTP_PORT`) e `docker compose stop simulador` antes do teste. Para mirar a API pela rede do Compose, troque por `--network sspd_default` e use `BASE_URL=http://nginx`.
- `-u "$(id -u)"` faz o k6 gravar o resumo com o seu usuário. Sem essa opção, o container não tem permissão para escrever em `resultados/`.
- Com `SENSORES=500`, o banco precisa de 500 pacientes (`SEED_PACIENTES=500` no `migrate`). Um paciente inexistente gera 404.

## Variáveis

| Variável | Padrão | Uso |
|---|---|---|
| `API_KEY` | obrigatória | o mesmo valor de `SENSOR_API_KEY` da API |
| `BASE_URL` | `http://localhost:8000` | endereço da API, sem `/leituras`. No deploy, o Nginx |
| `SENSORES` | 10 | usuários virtuais (10, 100 e 500 no relatório) |
| `N_PACIENTES` | 10 | o sensor *n* usa o paciente `P00n`. Com menos pacientes que sensores, os pacientes se repetem. No máximo `SEED_PACIENTES` |
| `INTERVALO` | 2 | segundos entre as leituras de cada sensor, como no simulador. `0` envia sem pausa (teste de estresse) |
| `DURACAO` | `1m` | duração do teste |
| `RESULTADOS` | `resultados` | pasta do resumo em JSON. Precisa existir |

A carga esperada é `SENSORES / INTERVALO` requisições por segundo: 5, 50 e 250 nos três cenários. Cada sensor espera um tempo aleatório antes da primeira leitura, para que não disparem todos no mesmo instante. Depois disso, mantém o ritmo descontando a latência da resposta.

## Resultado

No fim, o k6 escreve um resumo no terminal:

```
Teste de carga: POST /leituras em http://localhost:8000
  sensores=100 pacientes=100 intervalo=2s duracao=61.99s
  requisições: 3000 (48.4/s)  erros: 0.00%
  latência (ms): p50=8.12 p90=10.13 p95=10.93 p99=12.76 máx=64.39
  respostas: 201=3000 outros 2xx=0 4xx=0 5xx=0 sem resposta=0
  metas: erros < 1% OK | p95 < 500 ms OK
  resumo em resultados/carga-100-sensores-2026-11-26T12-51-32-910Z.json
```

O mesmo resumo vai para `resultados/carga-<N>-sensores-<data>.json`. Esses arquivos servem de base para a tabela e o gráfico do relatório. A pasta `resultados/` está no `.gitignore`: copie para o relatório os números que forem usados.

- **Latência:** tempo de resposta do `POST /leituras` (`http_req_duration`), com p50, p90, p95, p99 e máximo.
- **Taxa de erro:** fração de respostas que não são 2xx, incluindo timeouts (10 s).
- **Respostas:** `4xx` costuma ser API key errada, paciente inexistente ou rate limit. `5xx` é a API ou o banco com problema. `sem resposta` é conexão recusada ou timeout.
- **Metas:** menos de 1% de erros e p95 abaixo de 500 ms. Se alguma falhar, o k6 sai com código 99, mas o resumo é gravado do mesmo jeito.

Resultado do ensaio local, com um processo da API: [docs/backend.md](../docs/backend.md), seção 10.4.

CPU e memória dos containers ficam de fora do k6. Colete com `docker stats` durante o teste. O backlog do worker pode ser acompanhado com as consultas da seção 9 de [docs/integracao-infra.md](../docs/integracao-infra.md).

## Cuidados

- **Rate limit do Nginx:** todos os sensores do k6 saem do mesmo IP. Um limite por IP em `/leituras` corta o teste e aparece como `4xx` ou `5xx` (o `limit_req` do Nginx responde 503 por padrão). No teste de desempenho, o limite precisa comportar a carga do cenário ou ser desligado, e o relatório deve dizer qual configuração foi usada.
- **Onde roda o k6:** rodando na própria VPS, o k6 disputa a CPU com a API. De outra máquina, a latência inclui a rede. Registre no relatório de onde o teste foi feito.
- **Saturação:** cada sensor só envia a próxima leitura depois da resposta da anterior. Quando a API satura, a taxa de requisições fica abaixo da esperada e a latência sobe. Isso mostra o limite da configuração testada; não é erro do script.

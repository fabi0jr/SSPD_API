// Teste de desempenho do POST /leituras (etapa 8).
//
// Cada usuário virtual do k6 é um sensor: envia uma leitura a cada INTERVALO
// segundos para o paciente dele, com a API key, como o simulador. Os valores
// ficam dentro das faixas da API (nunca 422) e cada leitura tem um id novo.
//
// Uso (ver tests-carga/README.md):
//   docker run --rm --network host -u "$(id -u)" -v "$PWD":/carga -w /carga \
//     -e API_KEY=chave-local -e SENSORES=10 grafana/k6:2.3.0 run leituras.js
//
// Variáveis:
//   API_KEY      obrigatória, o mesmo valor de SENSOR_API_KEY da API
//   BASE_URL     padrão http://localhost:8000 (no deploy, a URL do Nginx)
//   SENSORES     usuários virtuais (10, 100, 500), padrão 10
//   N_PACIENTES  pacientes P001..Pnnn usados (no máximo SEED_PACIENTES), padrão 10
//   INTERVALO    segundos entre as leituras de cada sensor, padrão 2 como no simulador (0 = sem pausa)
//   DURACAO      duração do teste, padrão 1m
//   RESULTADOS   pasta do resumo em JSON, padrão resultados (precisa existir)

import http from "k6/http";
import exec from "k6/execution";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const API_KEY = __ENV.API_KEY;
if (!API_KEY) {
  throw new Error("defina API_KEY (o mesmo valor de SENSOR_API_KEY da API)");
}
const BASE_URL = (__ENV.BASE_URL || "http://localhost:8000").replace(/\/+$/, "");
const SENSORES = inteiro("SENSORES", 10);
const N_PACIENTES = inteiro("N_PACIENTES", 10);
const INTERVALO = Number(__ENV.INTERVALO ?? 2);
if (!Number.isFinite(INTERVALO) || INTERVALO < 0) {
  throw new Error("INTERVALO precisa ser um número maior ou igual a zero");
}
const DURACAO = __ENV.DURACAO || "1m";
const RESULTADOS = __ENV.RESULTADOS || "resultados";

const PROB_ANOMALIA_SPO2 = 0.02; // ~2% das leituras com SpO2 baixa, como o simulador

// Metas do teste (aparecem no resumo como OK ou FALHOU).
const META_TAXA_ERRO = 0.01; // menos de 1% de erros
const META_P95_MS = 500;

export const options = {
  scenarios: {
    sensores: {
      executor: "constant-vus",
      vus: SENSORES,
      duration: DURACAO,
      gracefulStop: "10s",
    },
  },
  thresholds: {
    http_req_failed: [`rate<${META_TAXA_ERRO}`],
    http_req_duration: [`p(95)<${META_P95_MS}`],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

// Respostas por classe, para separar erro da API (5xx), recusa (4xx, ex. rate
// limit) e falha de rede (status 0: timeout ou conexão recusada).
const respostas = {
  201: new Counter("respostas_201"),
  "2xx": new Counter("respostas_outros_2xx"),
  "4xx": new Counter("respostas_4xx"),
  "5xx": new Counter("respostas_5xx"),
  0: new Counter("sem_resposta"),
};

const HEADERS = { "Content-Type": "application/json", "X-API-Key": API_KEY };

export default function () {
  // Na primeira iteração, cada sensor espera um tempo aleatório para não
  // dispararem todos no mesmo instante.
  if (exec.vu.iterationInScenario === 0 && INTERVALO > 0) {
    sleep(Math.random() * INTERVALO);
  }

  const inicio = Date.now();
  const r = http.post(`${BASE_URL}/leituras`, JSON.stringify(gerarLeitura()), {
    headers: HEADERS,
    timeout: "10s",
    tags: { name: "POST /leituras" },
  });
  contar(r.status);
  check(r, { "status 201": (res) => res.status === 201 });

  // Mantém o ritmo de uma leitura por INTERVALO, descontando a latência.
  const restante = INTERVALO - (Date.now() - inicio) / 1000;
  if (restante > 0) sleep(restante);
}

function gerarLeitura() {
  const n = ((exec.vu.idInTest - 1) % N_PACIENTES) + 1;
  const anomalia = Math.random() < PROB_ANOMALIA_SPO2;
  return {
    id: crypto.randomUUID(),
    paciente_id: `P${String(n).padStart(3, "0")}`,
    fc: Math.round(uniforme(60, 100)),
    spo2: arredondar(anomalia ? uniforme(82, 89) : uniforme(95, 99.5)),
    temp: arredondar(uniforme(36.0, 37.3)),
    ts: new Date().toISOString(),
  };
}

function contar(status) {
  if (status === 201) respostas[201].add(1);
  else if (status >= 200 && status < 300) respostas["2xx"].add(1);
  else if (status >= 400 && status < 500) respostas["4xx"].add(1);
  else if (status >= 500) respostas["5xx"].add(1);
  else respostas[0].add(1);
}

export function handleSummary(data) {
  const m = data.metrics;
  const lat = m.http_req_duration.values;
  const total = (nome) => (m[nome] ? m[nome].values.count : 0);
  const meta = (nome) => Object.values(m[nome].thresholds || {}).every((t) => t.ok);

  const resumo = {
    teste: "POST /leituras",
    executado_em: new Date().toISOString(),
    parametros: {
      base_url: BASE_URL,
      sensores: SENSORES,
      n_pacientes: N_PACIENTES,
      intervalo_s: INTERVALO,
      duracao: DURACAO,
    },
    duracao_s: arredondar(data.state.testRunDurationMs / 1000),
    requisicoes: {
      total: m.http_reqs.values.count,
      por_segundo: arredondar(m.http_reqs.values.rate),
    },
    latencia_ms: {
      media: arredondar(lat.avg),
      min: arredondar(lat.min),
      p50: arredondar(lat.med),
      p90: arredondar(lat["p(90)"]),
      p95: arredondar(lat["p(95)"]),
      p99: arredondar(lat["p(99)"]),
      max: arredondar(lat.max),
    },
    taxa_erro: m.http_req_failed.values.rate,
    respostas: {
      201: total("respostas_201"),
      outros_2xx: total("respostas_outros_2xx"),
      "4xx": total("respostas_4xx"),
      "5xx": total("respostas_5xx"),
      sem_resposta: total("sem_resposta"),
    },
    metas: {
      taxa_erro_menor_que: { valor: META_TAXA_ERRO, ok: meta("http_req_failed") },
      p95_ms_menor_que: { valor: META_P95_MS, ok: meta("http_req_duration") },
    },
  };

  const carimbo = resumo.executado_em.replace(/[:.]/g, "-");
  const arquivo = `${RESULTADOS}/carga-${SENSORES}-sensores-${carimbo}.json`;
  return {
    stdout: textoResumo(resumo, arquivo),
    [arquivo]: JSON.stringify(resumo, null, 2) + "\n",
  };
}

function textoResumo(r, arquivo) {
  const p = r.parametros;
  const l = r.latencia_ms;
  const s = r.respostas;
  const ok = (v) => (v ? "OK" : "FALHOU");
  return [
    "",
    `Teste de carga: ${r.teste} em ${p.base_url}`,
    `  sensores=${p.sensores} pacientes=${p.n_pacientes} intervalo=${p.intervalo_s}s duracao=${r.duracao_s}s`,
    `  requisições: ${r.requisicoes.total} (${r.requisicoes.por_segundo}/s)  erros: ${(r.taxa_erro * 100).toFixed(2)}%`,
    `  latência (ms): p50=${l.p50} p90=${l.p90} p95=${l.p95} p99=${l.p99} máx=${l.max}`,
    `  respostas: 201=${s[201]} outros 2xx=${s.outros_2xx} 4xx=${s["4xx"]} 5xx=${s["5xx"]} sem resposta=${s.sem_resposta}`,
    `  metas: erros < ${META_TAXA_ERRO * 100}% ${ok(r.metas.taxa_erro_menor_que.ok)} | p95 < ${META_P95_MS} ms ${ok(r.metas.p95_ms_menor_que.ok)}`,
    `  resumo em ${arquivo}`,
    "",
  ].join("\n");
}

function inteiro(nome, padrao) {
  const v = __ENV[nome] === undefined ? padrao : Number(__ENV[nome]);
  if (!Number.isInteger(v) || v < 1) throw new Error(`${nome} precisa ser um inteiro maior que zero`);
  return v;
}

function uniforme(min, max) {
  return min + Math.random() * (max - min);
}

function arredondar(v) {
  return Math.round(v * 100) / 100;
}

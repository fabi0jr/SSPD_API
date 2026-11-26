"""Simulador de sensores de sinais vitais (CLAUDE.md, seção 12).

Uma thread por paciente (P001 .. P{N_PACIENTES}, os mesmos códigos do seed).
A cada INTERVALO segundos, cada sensor gera uma leitura e a envia para
POST /leituras com o header X-API-Key.

Tolerância a falhas: a leitura entra num buffer local (deque com no máximo
1000 itens por sensor) e só sai dele quando a API confirma com 2xx. Se a API
estiver fora, as leituras se acumulam e são reenviadas em ordem quando ela
voltar. O `id` (UUID) é gerado na criação da leitura, então um reenvio usa o
mesmo id e a API responde 200 sem duplicar (idempotência).

Só usa a biblioteca padrão. Dados fictícios.

Uso:  API_URL=http://localhost:8000/leituras SENSOR_API_KEY=... python simulador.py
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import sys
import threading
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger("simulador")

TAMANHO_BUFFER = 1000          # leituras por sensor; acima disso, a mais antiga é perdida
PROB_ANOMALIA_SPO2 = 0.02      # ~2% das leituras com queda de SpO2 (dispara SPO2_BAIXA)
TIMEOUT_HTTP = 5               # segundos
INTERVALO_RESUMO = 10          # segundos entre as linhas de resumo no log

# Limites para o random walk, dentro das faixas aceitas pela API
# (fc 20-250, spo2 50-100, temp 30-43): o simulador nunca gera 422.
FAIXA_FC = (30, 220)
FAIXA_SPO2 = (70.0, 100.0)
FAIXA_TEMP = (33.0, 42.0)

# Quanto o valor volta para a base a cada passo (0 = sem volta, 1 = sempre na base).
REVERSAO = 0.1


# ---------------------------------------------------------------- perfis ----

@dataclass(frozen=True)
class Perfil:
    nome: str
    fc: float
    spo2: float
    temp: float


PERFIS = {
    "estavel": Perfil("estavel", fc=75, spo2=97.5, temp=36.6),
    "febril": Perfil("febril", fc=102, spo2=96.5, temp=38.4),          # FEBRE frequente
    "hipoxemico": Perfil("hipoxemico", fc=90, spo2=91.0, temp=36.8),   # SPO2_BAIXA de vez em quando
}


def perfil_do_paciente(n: int) -> Perfil:
    """Distribuição fixa: a maioria estável, alguns febris e hipoxêmicos."""
    if n % 10 == 3:
        return PERFIS["febril"]
    if n % 10 == 7:
        return PERFIS["hipoxemico"]
    return PERFIS["estavel"]


def codigo_paciente(n: int) -> str:
    return f"P{n:03d}"


# ------------------------------------------------------------- resultado ----

NOVA = "nova"              # 201
DUPLICADA = "duplicada"    # 200: o id já existia (reenvio)
REJEITADA = "rejeitada"    # 4xx definitivo: reenviar não adianta, a leitura é descartada
FALHA = "falha"            # rede, timeout, 5xx, 408, 429: fica no buffer


def classificar(status: int | None) -> str:
    if status is None:
        return FALHA
    if 200 <= status < 300:
        return DUPLICADA if status == 200 else NOVA
    if 400 <= status < 500 and status not in (408, 429):
        return REJEITADA
    return FALHA


@dataclass(frozen=True)
class Resposta:
    status: int | None     # None = sem resposta (rede ou timeout)
    detalhe: str = ""


Enviar = Callable[[dict], Resposta]


def criar_enviador_http(url: str, api_key: str, timeout: float = TIMEOUT_HTTP) -> Enviar:
    def enviar(leitura: dict) -> Resposta:
        requisicao = urllib.request.Request(
            url,
            data=json.dumps(leitura).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
                return Resposta(resposta.status)
        except urllib.error.HTTPError as erro:  # resposta com status 4xx/5xx
            corpo = erro.read(300).decode("utf-8", "replace")
            return Resposta(erro.code, corpo)
        except (urllib.error.URLError, OSError) as erro:  # conexão recusada, DNS, timeout
            motivo = getattr(erro, "reason", erro)
            return Resposta(None, f"{type(erro).__name__}: {motivo}")

    return enviar


# ----------------------------------------------------------- estatísticas ---

class Estatisticas:
    """Contadores globais (todas as threads) e detecção de queda/volta da API."""

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self.contagem = {NOVA: 0, DUPLICADA: 0, REJEITADA: 0, FALHA: 0}
        self.perdidas = 0          # descartadas pelo buffer cheio
        self.api_disponivel = True

    def registrar(self, paciente_id: str, resultado: str, resposta: Resposta) -> None:
        with self._trava:
            self.contagem[resultado] += 1
            if resultado == FALHA and self.api_disponivel:
                self.api_disponivel = False
                motivo = resposta.detalhe if resposta.status is None else f"HTTP {resposta.status}"
                log.warning("API indisponível (%s); leituras ficam no buffer", motivo)
            elif resultado in (NOVA, DUPLICADA) and not self.api_disponivel:
                self.api_disponivel = True
                log.info("API disponível novamente; reenviando o buffer")
        if resultado == REJEITADA:
            log.warning("leitura de %s rejeitada (HTTP %s) e descartada: %s",
                        paciente_id, resposta.status, resposta.detalhe)

    def perdida(self, paciente_id: str) -> None:
        with self._trava:
            self.perdidas += 1
            primeira = self.perdidas == 1
        if primeira:
            log.warning("buffer de %s cheio (%d): descartando as leituras mais antigas",
                        paciente_id, TAMANHO_BUFFER)

    def resumo(self, buffer_total: int) -> str:
        with self._trava:
            c = dict(self.contagem)
            perdidas = self.perdidas
        enviadas = c[NOVA] + c[DUPLICADA]
        return (f"enviadas={enviadas} novas={c[NOVA]} duplicadas={c[DUPLICADA]} "
                f"rejeitadas={c[REJEITADA]} falhas={c[FALHA]} perdidas={perdidas} "
                f"buffer={buffer_total}")


# ----------------------------------------------------------------- sensor ---

def _limitar(valor: float, faixa: tuple[float, float]) -> float:
    return max(faixa[0], min(faixa[1], valor))


def agora_iso() -> str:
    """ISO 8601 em UTC, com milissegundos e Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Sensor:
    def __init__(
        self,
        paciente_id: str,
        perfil: Perfil,
        enviar: Enviar,
        estatisticas: Estatisticas,
        *,
        rng: random.Random | None = None,
        relogio: Callable[[], str] = agora_iso,
        tamanho_buffer: int = TAMANHO_BUFFER,
    ) -> None:
        self.paciente_id = paciente_id
        self.perfil = perfil
        self.enviar = enviar
        self.estatisticas = estatisticas
        self.rng = rng or random.Random()
        self.relogio = relogio
        self.buffer: deque[dict] = deque(maxlen=tamanho_buffer)
        self.fc = perfil.fc
        self.spo2 = perfil.spo2
        self.temp = perfil.temp

    def _passo(self, atual: float, base: float, desvio: float, faixa: tuple[float, float]) -> float:
        return _limitar(atual + REVERSAO * (base - atual) + self.rng.gauss(0, desvio), faixa)

    def gerar(self) -> dict:
        """Nova leitura: random walk em volta da base do perfil. O id nasce aqui."""
        self.fc = self._passo(self.fc, self.perfil.fc, 2.0, FAIXA_FC)
        self.spo2 = self._passo(self.spo2, self.perfil.spo2, 0.3, FAIXA_SPO2)
        self.temp = self._passo(self.temp, self.perfil.temp, 0.05, FAIXA_TEMP)
        spo2 = self.spo2
        if self.rng.random() < PROB_ANOMALIA_SPO2:
            spo2 = self.rng.uniform(82.0, 89.0)  # queda pontual, não altera o random walk
        return {
            "id": str(uuid.uuid4()),
            "paciente_id": self.paciente_id,
            "fc": round(self.fc),
            "spo2": round(spo2, 1),
            "temp": round(self.temp, 1),
            "ts": self.relogio(),
        }

    def enfileirar(self, leitura: dict) -> None:
        if len(self.buffer) == self.buffer.maxlen:
            self.estatisticas.perdida(self.paciente_id)  # o append descarta a mais antiga
        self.buffer.append(leitura)

    def esvaziar(self, parar: threading.Event | None = None) -> None:
        """Envia o buffer em ordem. Para na primeira falha: o resto espera o próximo ciclo."""
        while self.buffer and not (parar and parar.is_set()):
            leitura = self.buffer[0]
            resposta = self.enviar(leitura)
            resultado = classificar(resposta.status)
            self.estatisticas.registrar(self.paciente_id, resultado, resposta)
            if resultado == FALHA:
                return
            self.buffer.popleft()

    def ciclo(self, parar: threading.Event | None = None) -> None:
        self.enfileirar(self.gerar())
        self.esvaziar(parar)

    def rodar(self, parar: threading.Event, intervalo: float) -> None:
        # Atraso inicial aleatório, para os sensores não enviarem todos juntos.
        if parar.wait(self.rng.uniform(0, intervalo)):
            return
        while not parar.is_set():
            self.ciclo(parar)
            parar.wait(intervalo)


# --------------------------------------------------------------- execução ---

class ConfiguracaoInvalida(RuntimeError):
    pass


@dataclass(frozen=True)
class Configuracao:
    api_url: str
    api_key: str
    n_pacientes: int
    intervalo: float


def ler_configuracao(ambiente: dict[str, str]) -> Configuracao:
    def obrigatoria(nome: str) -> str:
        valor = ambiente.get(nome, "").strip()
        if not valor:
            raise ConfiguracaoInvalida(f"variável de ambiente {nome} não definida")
        return valor

    def positivo(nome: str, padrao: str, tipo: type) -> float:
        bruto = ambiente.get(nome, "").strip() or padrao
        try:
            valor = tipo(bruto)
        except ValueError:
            raise ConfiguracaoInvalida(f"variável de ambiente {nome} inválida (recebido {bruto!r})") from None
        if valor <= 0:
            raise ConfiguracaoInvalida(f"variável de ambiente {nome} precisa ser maior que zero")
        return valor

    return Configuracao(
        api_url=obrigatoria("API_URL"),
        api_key=obrigatoria("SENSOR_API_KEY"),
        n_pacientes=int(positivo("N_PACIENTES", "10", int)),
        intervalo=float(positivo("INTERVALO", "2", float)),
    )


def configurar_logs() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )


def main() -> int:
    configurar_logs()
    try:
        cfg = ler_configuracao(dict(os.environ))
    except ConfiguracaoInvalida as erro:
        log.error("erro de configuração: %s", erro)
        return 1

    parar = threading.Event()

    def ao_receber_sinal(numero, _frame) -> None:
        log.info("%s recebido; encerrando", signal.Signals(numero).name)
        parar.set()

    signal.signal(signal.SIGTERM, ao_receber_sinal)
    signal.signal(signal.SIGINT, ao_receber_sinal)

    estatisticas = Estatisticas()
    enviar = criar_enviador_http(cfg.api_url, cfg.api_key)
    sensores = [
        Sensor(codigo_paciente(n), perfil_do_paciente(n), enviar, estatisticas)
        for n in range(1, cfg.n_pacientes + 1)
    ]
    threads = [
        threading.Thread(target=s.rodar, args=(parar, cfg.intervalo), name=s.paciente_id, daemon=True)
        for s in sensores
    ]
    log.info("iniciando %d sensores: url=%s intervalo=%ss buffer=%d por sensor",
             len(sensores), cfg.api_url, cfg.intervalo, TAMANHO_BUFFER)
    for t in threads:
        t.start()

    def buffer_total() -> int:
        return sum(len(s.buffer) for s in sensores)

    while not parar.wait(INTERVALO_RESUMO):
        log.info("resumo: %s", estatisticas.resumo(buffer_total()))

    for t in threads:
        t.join(timeout=TIMEOUT_HTTP + 1)
    log.info("resumo final: %s", estatisticas.resumo(buffer_total()))
    restantes = buffer_total()
    if restantes:
        log.warning("%d leitura(s) ainda no buffer foram perdidas no encerramento", restantes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Caso de uso: um ciclo do worker de alertas."""

from app.dominio import regras
from app.dominio.entidades import Alerta, ResultadoCiclo, TipoAlerta
from app.dominio.portas import FilaPendentes, LotePendente


class ProcessarPendentes:
    def __init__(self, fila: FilaPendentes) -> None:
        self._fila = fila

    def executar(self, limite: int) -> ResultadoCiclo:
        """Reserva até `limite` leituras pendentes, gera os alertas e marca o lote.

        Tudo dentro do `with` é uma transação só: se algo falhar, a exceção
        sobe, o adaptador faz rollback e o lote é reprocessado no próximo ciclo.
        """
        with self._fila.reservar(limite) as lote:
            novos = self._gerar_alertas(lote)
            if novos:
                lote.registrar_alertas(novos)
            if lote.leituras:
                lote.marcar_processadas()
            processadas = len(lote.leituras)

        # Depois do commit, para o log refletir o que de fato ficou pendente.
        backlog = self._fila.contar_pendentes()
        return ResultadoCiclo(
            processadas=processadas,
            alertas_criados=len(novos),
            backlog=backlog,
        )

    @staticmethod
    def _gerar_alertas(lote: LotePendente) -> list[Alerta]:
        # Anti-spam: por paciente, os tipos que não podem gerar alerta novo.
        # Começa com os abertos no banco e cresce com os gerados neste lote,
        # porque tipos_abertos só enxerga o que já está gravado.
        bloqueados: dict[str, set[TipoAlerta]] = {}
        novos: list[Alerta] = []

        for leitura in lote.leituras:
            tipos = bloqueados.get(leitura.paciente_id)
            if tipos is None:
                tipos = set(lote.tipos_abertos(leitura.paciente_id))
                bloqueados[leitura.paciente_id] = tipos

            for alerta in regras.avaliar(leitura):
                if alerta.tipo in tipos:
                    continue
                tipos.add(alerta.tipo)
                novos.append(alerta)

        return novos

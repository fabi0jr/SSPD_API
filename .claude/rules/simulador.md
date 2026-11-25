---
paths:
  - "simulador/**"
---

# Simulador

Implementado na etapa 7: `simulador/simulador.py` (um arquivo, só biblioteca padrão, envio com `urllib`), testes em `simulador/tests/` e `simulador/Dockerfile`. Descrição completa: `docs/backend.md`, seção 11.1.

Decisões que precisam se manter:
- Uma thread por paciente (`P001`..`P{N_PACIENTES}`, os códigos do seed), random walk com reversão à base do perfil, valores presos dentro das faixas da API (nunca gerar 422)
- Perfis fixos pelo número do paciente: final 3 febril, final 7 hipoxêmico, demais estáveis. Anomalia de SpO₂ (82–89) em ~2% das leituras
- `id` (`uuid.uuid4()`) e `ts` gerados **na criação da leitura**, nunca no envio: o reenvio usa o mesmo `id` (idempotência)
- Header `X-API-Key`. `200` e `201` são sucesso
- Buffer `deque(maxlen=1000)` **por sensor**, reenviado em ordem; para na primeira falha. Falha temporária (sem resposta, 5xx, 408, 429) fica no buffer; outros 4xx são descartados com WARNING. Buffer cheio descarta a mais antiga e conta em `perdidas`
- Logs: resumo a cada 10 s (`enviadas novas duplicadas rejeitadas falhas perdidas buffer`, totais acumulados) e uma linha só na queda e na volta da API. Os testes de falha usam essas linhas como evidência: não mudar o formato sem atualizar `docs/integracao-infra.md`, seção 7
- SIGTERM: para as threads, escreve o resumo final e avisa o que ficou no buffer. CMD em forma exec
- `timeout` de 5 s no envio não cobre o DNS: apontando direto para um container parado, a falha demora ~10 s para aparecer (no deploy, `API_URL` aponta para o Nginx)

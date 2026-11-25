---
paths:
  - "simulador/**"
---

# Simulador

A pasta `simulador/` ainda não existe, e o script inicial citado abaixo não está no repositório.

Base: script Python com uma thread por paciente, variação por random walk, anomalia ocasional de SpO₂ (~2%) e buffer `deque(maxlen=1000)` com retry.

Ajustes necessários em relação à versão inicial:
- Gerar `id` com `uuid.uuid4()` **no momento da criação da leitura**, e não no envio
- Enviar `X-API-Key` em vez de `Authorization: Bearer`
- Tratar `200` e `201` como sucesso
- Log com a quantidade enviada, o tamanho do buffer e as falhas, para as evidências
- Opcional: perfis de paciente (estável, febril, hipoxêmico)

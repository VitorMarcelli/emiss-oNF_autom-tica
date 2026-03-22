# Matriz de Cobertura de UFs — Emissão de Guias ICMS

Esta matriz documenta o status real de validação de cada UF, confirmando os dados obrigatórios e a integridade da geração do PDF em ambiente limpo do cliente.

> Última atualização: 2026-03-21

## Status Detalhado por UF

| UF | Método Usado | Receita Validada | Contribuinte (Testado) | Campos Obrigatórios (Ocultos/Extras) | Data Venc. | Data Pag. | Acréscimos | PDF | Status | Observações |
|----|--------------|------------------|------------------------|--------------------------------------|------------|-----------|------------|-----|--------|-------------|
| **GO** | Híbrido (Playwright) | 108 (ICMS - NORMAL) | `10.410.432-5` (Inscrito) | `detalhe_apuracao` (Ex: dia) | SIM | SIM | NÃO | SIM | ✅ Concluído | Playwright contorna bloqueio CAPTCHA interagindo com árvore de receitas PrimeFaces. Campos de data e detalhe de apuração são forçados via automação JS. Falhas no eval() corrigidas. |
| **MT** | HTTP Direto (`requests`) | 1112 | `133201040` (Inscrito) | N/A | SIM | NÃO | SIM | SIM | ✅ Concluído | Data de vencimento corrigida para recepção via payload. Suporta acréscimos (`juros`, `multa`, `correção`) no form. |
| **MG** | HTTP Direto (`requests`) | ICMS APURADO NO PERIODO | `16.670.085/0001-55` (Inscrito/Test) | N/A | SIM | SIM | SIM | SIM | ✅ Concluído | Bloqueio de 'Falha não especificada' ocorria devido a formatação de datas em finais de semana. Datas tornaram-se dinâmicas ('dados_emissao'). Suporta impressão. |
| **SP** | HTTP Direto (`requests`) | 046-2 | `51.789.601/0001-66` (Bradesco Test) | N/A | SIM | SIM | SIM | SIM | ✅ Concluído | Restrição CAPTCHA nativamente contornada via bypass do Endpoint Secundário de validação. Datas de vencimento customizáveis inseridas no payload HTTP. |
| **PR** | Híbrido (Playwright / HTTP) | 1015 | `9017315606` (Super Muffato IE Test) | N/A | SIM | SIM | SIM | SIM | ✅ Concluído | Bloqueio reCAPTCHA Enterprise contornado através de rotina nativa em Playwright que navega o portal de forma automatizada. Data de pagamento e vencimento dinâmicas. |

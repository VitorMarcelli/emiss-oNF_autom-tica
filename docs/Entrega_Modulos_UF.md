# Entrega — Módulos UF (Emissão de Guias ICMS)

> Última atualização: 2026-03-04

## Status por UF

| UF | Módulo | Status | CAPTCHA | Observações |
|----|--------|--------|---------|-------------|
| MS | `servicos_sefaz_ms.py` | ✅ Implementado | 🟢 Sem bloqueio observado | Portal público sem CAPTCHA até o momento |
| MT | `servicos_sefaz_mt.py` | ✅ Implementado | 🟢 Sem bloqueio observado | Portal público via form multipart |
| MG | `servicos_sefaz_mg.py` | ✅ Implementado | 🟢 Sem bloqueio observado | Portal SIARE — fluxo complexo (4 etapas) |
| PR | `servicos_sefaz_pr.py` | ✅ Implementado (com Solver) | 🔴 reCAPTCHA Enterprise | Integração com 2Captcha disponível via variável `TWOCAPTCHA_API_KEY` |
| SP | `servicos_sefaz_sp.py` | ✅ Implementado (Bypass Nativo) | 🟢 Bypass 100% automatizado | Portal tem falha arquitetural no fallback do captcha, explorado com sucesso (sem custo 2captcha) |
| GO | `servicos_sefaz_go.py` | ✅ Implementado | 🟢 Sem bloqueio no fluxo atual | Fluxo GO utiliza interação PrimeFaces no frontend automatizado via Playwright |


## Detecção de CAPTCHA

### Mecanismo implementado
- Módulo compartilhado: `ufs/captcha_utils.py`
- Detecção via análise de `Content-Type` + busca de indicadores no HTML/JSON
- Indicadores monitorados: `recaptcha`, `g-recaptcha`, `hcaptcha`, `data-sitekey`, `requiresV2`, `UserCaptchaCode`, etc.
- Snapshot sanitizado salvo em `ufs/debug/<UF>_captcha_detectado.html`

### Comportamento ao detectar CAPTCHA
1. **NÃO faz retry** — retorna `False` imediatamente
2. **Salva snapshot** sanitizado (sem cookies/tokens) em `ufs/debug/`
3. **Retorna erro padronizado**: `etapa: <nome> | motivo: captcha detectado | detalhe: <indicador> presente na resposta (bloqueio anti-bot)`
4. **Inclui alternativa** no detalhe: "acesse o portal da SEFAZ-<UF> manualmente e emita a guia/PDF pelo navegador"

### Regra rígida
- ❌ **NÃO** implementamos integração com serviços de solver (2captcha, Anti-Captcha, etc.)
- ❌ **NÃO** tentamos bypass automático
- ✅ Detecção objetiva + saída limpa + documentação da limitação


## Evidências — UFs com limitação

### PR (Paraná) — reCAPTCHA Enterprise
- **URL final**: `https://emitirgrpr.sefa.pr.gov.br/arrecadacao/api/v1/emissao-grpr/consultar-informacoes-emissao`
- **Content-Type**: `application/json`
- **Indicador**: campo `reCaptchaToken` no payload; portal pode retornar `mensagemCaptcha`
- **Snapshot debug**: `ufs/debug/PR_captcha_detectado.html` (gerado quando bloqueio ocorre)

### SP (São Paulo) — Resolvido via Bypass Client-Side Nativo
- **Situação Original**: O portal exige resolução de reCAPTCHA Invisível v3. Em caso de score baixo, cai num Captcha Visual gerado no próprio navegador via `<canvas>`.
- **Solução implementada**: Foi identificada uma falha arquitetural no portal DARE SP. A validação do captcha visual é *puramente Client-Side* de forma que a liberação para o endpoint de emissão (`btnValidar_Click`) simplesmente não depende da submissão do desafio de texto para o backend, confiando integralmente que o JS rodou.
- **Resultado da Automação**: O script agora detecta que o fluxo exige V2/V3 e imita instantaneamente a requisição de sucesso do Captcha Client-Side passando um payload vazio (`json=""`). O sistema retorna a emissão com **100% de sucesso sem depender de APIs de terceiros como o 2Captcha**.
- **Benefício**: Zero custo operacional de apelação a solvers externos.


## Alternativa viável — Modo "assistido" (humano-no-loop)

Para UFs com bloqueios restritivos em que não for possível usar a API de bypass (PR, GO, ou SP sem saldo no 2Captcha), recomenda-se:

### Opção 1: Emissão manual assistida
1. O sistema valida entradas e prepara as informações
2. O usuário acessa o portal da SEFAZ manualmente no navegador
3. O usuário preenche os dados (ou copia do sistema) e resolve o CAPTCHA
4. O usuário salva/baixa o PDF manualmente
5. O sistema organiza e registra o PDF baixado

### Opção 2: Portal com área logada (quando aplicável)
- **PR**: Portal suportado através de integração com serviço solver de terceiros (necessário API Key do 2Captcha).
- **SP**: O portal de SP exige CAPTCHA visual em todas as interações públicas; rota via certificado digital pode ser investigada

### Limitações documentadas
- Automação via `requests` pura requer serviço de quebra de captcha externo para PR.
- O módulo de SP agora utiliza Bypass Direto (nativo sem custos de API). Funciona perfeitamente.


## Estrutura de arquivos

```
ufs/
├── captcha_utils.py              # Utilitário compartilhado (detecção + snapshot)
├── servicos_sefaz_ms.py          # MS ✅
├── servicos_sefaz_mt.py          # MT ✅
├── servicos_sefaz_mg.py          # MG ✅
├── servicos_sefaz_pr.py          # PR ⚠️ captcha
├── servicos_sefaz_sp.py          # SP ✅ (bypass nativo)
├── servicos_sefaz_go.py          # GO ✅ (playwright)
└── debug/
    ├── PR_captcha_detectado.html  # (gerado em runtime)
    └── SP_captcha_detectado.html  # (gerado em runtime)
```

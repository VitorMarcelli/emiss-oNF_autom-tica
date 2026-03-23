# Contrato de Entrada (SEFAZ-PR)

## 1. Visão Geral da UF
* **Arquivo do Motor:** `servicos_sefaz_pr.py`
* **Função Principal:** `emitir`
* **Resumo:** Gera emissões via automação Playwright-RPA no sistema DARE-PR. Devido à pesadíssima matriz de receitas tributárias com IDs ofuscados (Vue.js Multiselect), o sistema PR expurga falsos positivos impedindo defaults genéricos. Trabalha com solver de captcha terceirizado em seu fluxo restrito.

## 2. Contrato de `dados_emissao`

| Campo | Obrigatório? | Tipo | Formato / Regra |
|---|---|---|---|
| `ie_cnpj` | **SIM** | `String` | Documento usado na escolha do Tipo de Identificação (CNPJ/CPF/IE). |
| `receita_codigo` | **SIM** | `String` (Numérica) | Código numérico puro da GIA/Receita. (ex: `"1015"`). O módulo rejeita textos arbitrários levantando exceção explícita de `ValueError`. |
| `valor` | **SIM** | `Float` | Sem travas rigorosas além do > 0.0. |
| `referencia` | **SIM** | `String` | `MM/AAAA`. Imprescindível. |
| `data_vencimento` | Opcional | `String` | PR aceita guias em atraso e preenche dados. |
| `data_pagamento` | Opcional | `String` | Dita a geração de recálculos subjacentes caso vencido. |

## 3. Regras Específicas da UF
* **Vencida / Não Vencida:** As duas vertentes são toleradas. 
* **Cálculo Automático/Manual:** O PR refaz as estimativas no formulário se houver competência para trás caso a receita selecionada obrigue mora. A automação injeta as datas e aciona o DOM.
* **Código vs Nome do Tributo:** CÓDIGO APENAS. A listagem do Paraná embute hashes escondidos via Vue.js, e o código força um match restrito `str(codigo) in div.text()`.
* **Captcha & Fallback:** A automação Paraná detecta implantes passivos de ReCaptcha V3 Entreprise. A rotina dependerá explicitamente da injeção no ambiente `.env` de var `TWOCAPTCHA_API_KEY`. Se a chave inexiste, levanta `Fail-Fast: Solver não configurado`.
* **Proteção contra PDF Fake:** A rotina do Playwright checa magic numbers (`%PDF-`) de 4 bytes e peso > 0KB na entrega para não gerar falsos HTML baixados como pdfs de PR.

## 4. Falhas Esperadas
| O que Bloqueia | Mensagem/Tipagem | Limitação Técnica |
|---|---|---|
| Timeout de UI/Multiselect | `Falha na seleção da receita. As opções Vue congelaram` | RPAs do PR tendem a crashear se o Chromium rodar OOM em ambientes limpos repetitivamente. |
| Receita Não Encontrada | `ValueError: Código de Receita "xxx" não corresponde estritamente` | A antiga prática de selecionar fallback via index `[0]` foi abolida para frear guias mentirosas de valores falsos. |

## 5. Exemplos

### 🟢 Payload Funcional
```python
dados_emissao = {
    "ie_cnpj": "12345678909",
    "receita_codigo": "1015", # Codigo exato aceito
    "valor": 95.80,
    "referencia": "04/2026",
    "data_vencimento": "10/05/2026"
}
```

### 🔴 Payload que Deve Falhar
```python
dados_emissao = {
    "ie_cnpj": "12345678909",
    "receita_codigo": "ICMS Parana", # Texto rejeitado
    "valor": 95.80,
}
```
**Motivo:** A instrução nativa barrará letras. "ICMS Parana" não tem representação numérica estrita. Adicionalmente, se faltar `referencia`, rechaça instantaneamente sem nem instanciar o browser (`ValueError`).

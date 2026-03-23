# Contrato de Entrada (SEFAZ-MS)

## 1. Visão Geral da UF
* **Arquivo do Motor:** `servicos_sefaz_ms.py`
* **Função Principal:** `emitir`
* **Resumo:** Dispara automação assíncrona Webkit (Playwright) para o portal de Mato Grosso do Sul (DAEMS), utilizando interação Chromium autônoma para transpor o painel do contribuinte.

## 2. Contrato de `dados_emissao`

| Campo | Obrigatório? | Tipo | Formato / Regra |
|---|---|---|---|
| `ie_cnpj` | **SIM** | `String` | Documento (Inscrição Estadual ou CNPJ) no estado de MS. |
| `receita_codigo` | **SIM** | `String` | EXIGÊNCIA NUMÉRICA (ex: `"004"` ou `"046"`). Não escreva textos. |
| `valor` | **SIM** | `Float` | Sem travas extremas, mas não deve zerar. |
| `referencia` | **SIM** | `String` | `MM/AAAA`. |
| `data_vencimento` | Opcional | `String` | Opcionalmente determina juros e o motor MS propaga. |
| `data_pagamento` | Opcional | `String` | Motor aplica fallback herdando do vencimento se não presente. |
| `observacao` | Opcional | `String` | Historico inserido logo abaixo dos dados no DAEMS. |

## 3. Regras Específicas da UF
* **Vencida / Não Vencida:** As duas vertentes são bem suportadas na caixa de diálogo de MS.
* **Cálculo Automático/Manual:** O Portal não impõe barreiras ao valor estipulado via motor local, suportando imputar vencimentos velhos com o valor passado no dicionário da Python. 
* **Código vs Nome do Tributo:** O portal tem auto-complete que varre o ID do tributo. Repassar código!
* **Captcha:** N/A. MS utiliza arquitetura sem barreira agressiva anti-bot na tela referida.

## 4. Falhas Esperadas
| O que Bloqueia | Mensagem/Tipagem | Limitação Técnica |
|---|---|---|
| Timeout Playwright | Erro de localizador (Locator Timeout 10s) | Por não ser API pura (é RPA UI-Bound), a lentidão do portal MS pode exceder o timer padrão. |

## 5. Exemplos

### 🟢 Payload Funcional
```python
dados_emissao = {
    "ie_cnpj": "282828285",
    "receita_codigo": "004",   # Codigo Numérico puro
    "valor": 105.50,
    "referencia": "12/2025"
}
```

### 🔴 Payload que Deve Falhar
```python
dados_emissao = {
    "ie_cnpj": "282828285",
    "receita_codigo": "ICMS", # ERRO: Nao é ID
    "valor": 105.50,
}
```
**Motivo:** Erro de Match. A caixa de pesquisa reativa do React/Vue do Sefaz-MS não auto-sugerirá `004` caso você digite `ICMS`, travando a automação de seleção suspensa do playwright.

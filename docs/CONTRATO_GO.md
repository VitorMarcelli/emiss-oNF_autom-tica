# Contrato de Entrada (SEFAZ-GO)

## 1. Visão Geral da UF
* **Arquivo do Motor:** `servicos_sefaz_go.py`
* **Função Principal:** `emitir`
* **Resumo:** Automatiza a emissão de DARE em Goiás, englobando receitas diárias ou complementares, interligando a transação via rede estadual sem solvers de captcha. Lida com juros recalculados dinamicamente via Portal.

## 2. Contrato de `dados_emissao`

| Campo | Obrigatório? | Tipo | Formato / Regra |
|---|---|---|---|
| `ie_cnpj` | **SIM** | `String` | CNPJ ou IE. O motor consulta o documento para extrair RAZÃO SOCIAL antes da emissão. |
| `receita_codigo` | **SIM** | `String` | Formato restrito (ex: `"1002"`, `"400-0"` etc). |
| `valor` | **SIM** | `Float` | Ex: `150.0`. Não pode ser <= 0. |
| `referencia` | **SIM** | `String` | `MM/AAAA`. Impede a suposição fantasiosa de mês vazio. |
| `tipo_referencia` | **SIM** | `String` | Receitas em GO possuem categorização obrigatória. Valores válidos limitados a: `"diaria"` ou `"complementar"`. |
| `data_vencimento` | **SIM** | `String` | `DD/MM/AAAA`. Passada imperativamente ao DARE. |
| `data_pagamento` | Opcional | `String` | `DD/MM/AAAA`. Se falto, assimila-se ao vencimento. |

## 3. Regras Específicas da UF
* **Vencida / Não Vencida:** As duas vertentes operam plenamente.
* **Cálculo Automático/Manual:** Automático. Goiás preenche e recalcula `total com juros e multa` diretamente sobre a injeção do vencimento. Você não envia campos locais de juros.
* **Código vs Nome do Tributo:** Operação via CÓDIGO. Não passe nomes escritos.
* **Exigência de Acréscimos/Tipo Referência:** Não aceita dicionário `acrescimos`. EXIGE o envio do parâmetro estrutural `tipo_referencia`. Sem preenchimento não há default.
* **Captcha:** N/A.

## 4. Falhas Esperadas
| O que Bloqueia | Mensagem/Tipagem | Limitação Técnica |
|---|---|---|
| `tipo_referencia` ausente | **Fail-Fast** `referencia ou formato inválido` | GO demanda click em modal explícito (`Complementar` vs `Mensal/Diária`). Não advinhamos. |
| Timeout na Sefaz | Erro transacional HTTP 500/504 | O portal de GO oscila sob carga à tarde. O script realiza *fallback* de retentativas programadas antes de ceder. |

## 5. Exemplos

### 🟢 Payload Funcional
```python
dados_emissao = {
    "ie_cnpj": "01.002.003/0004-05",
    "receita_codigo": "1002",
    "valor": 105.50,
    "referencia": "12/2025",
    "tipo_referencia": "diaria",
    "data_vencimento": "15/01/2026",
    "data_pagamento": "15/01/2026"
}
```

### 🔴 Payload que Deve Falhar
```python
dados_emissao = {
    "ie_cnpj": "01.234.567/0001-89",
    "receita_codigo": "ICMS Mensal", # ERRO: Passando NOME ao invés de Código
    "valor": 105.50
    # ERRO: Sem tipo_referencia e sem vencimento
}
```
**Motivo:** GO requer código (ID) explícito para a select box (ex: `"1002"`) e o tipo temporal. O código levantará falha acusando ausência desses parâmetros-chave e será encerrado prematuramente pela trava do payload.

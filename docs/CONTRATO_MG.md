# Contrato de Entrada (SEFAZ-MG)

## 1. Visão Geral da UF
* **Arquivo do Motor:** `servicos_sefaz_mg.py`
* **Função Principal:** `emitir`
* **Resumo:** Automatiza DAE via SIARE de Minas Gerais. O módulo reproduz fielmente o fluxo do portal, incluindo a etapa de cálculo de multa/juros para guias vencidas.

## 2. Contrato de `dados_emissao`

| Campo | Obrigatório? | Tipo | Formato / Regra |
|---|---|---|---|
| `ie_cnpj` | **SIM** | `String` | CNPJ, CPF ou IE livre. |
| `receita_codigo` | **SIM** | `String` | Nome textual exato (ex: `"ICMS APURADO NO PERIODO"`). |
| `valor` | **SIM** | `Float` | `> 0.0`. |
| `referencia` | **SIM** | `String` | `MM/AAAA`. |
| `data_vencimento` | **SIM** | `String` | `DD/MM/AAAA`. |
| `data_pagamento` | Opcional | `String` | `DD/MM/AAAA`. Se ausente, assume `data_vencimento`. |

## 3. Regras Específicas da UF
* **Guia Vencida:** Quando `data_pagamento > data_vencimento`, o módulo aciona automaticamente a etapa **CALCULAR MULTA/JUROS** do portal SIARE. Os valores de multa, juros e total são retornados pelo próprio portal e usados na geração do DAE. **Nenhum cálculo local é realizado.**
* **Código vs Nome do Tributo:** Por ser SelectOneMenu preenchido com descrições atrelado ao CNPJ de cada cliente, utilizamos o *Match Exato/Parcial por String*.
* **Captcha:** O Portal (SIARE DARE Autônomo) não exige Captcha neste Endpoint público.

## 4. Falhas Esperadas
| O que Bloqueia | Mensagem/Tipagem | Causa |
|---|---|---|
| Falta de match da Receita | `etapa: selecionar_receita \| motivo: receita não encontrada` | IE não possui a receita informada; erro retorna as opções reais. |
| IE inválida | `etapa: validar_ie \| motivo: IE inválida segundo o portal` | Documento não reconhecido pelo SIARE. |
| Erro do portal no cálculo | Warning no log com msg do portal | Portal retornou erro ao calcular multa/juros (ex: receita incompatível). |

## 5. Exemplos

### 🟢 Payload Funcional (guia em dia)
```python
dados_emissao = {
    "ie_cnpj": "062307904.00-81",
    "receita_codigo": "ICMS MINERAIS",
    "valor": 500,
    "referencia": "01/2026",
    "data_vencimento": "20/03/2026"
}
```

### 🟢 Payload Funcional (guia vencida)
```python
dados_emissao = {
    "ie_cnpj": "062307904.00-81",
    "receita_codigo": "ICMS MINERAIS",
    "valor": 500,
    "referencia": "02/2026",
    "data_vencimento": "20/02/2026",
    "data_pagamento": "23/03/2026"
}
```
*Multa e juros serão calculados automaticamente pelo portal SIARE.*

### 🔴 Payload que Deve Falhar
```python
dados_emissao = {
    "ie_cnpj": "123.456",
    "receita_codigo": "1002",
    "valor": 100,
    "referencia": "01/2010"
}
```
**Motivo:** IE inválida e receita não encontrada no dropdown do portal.

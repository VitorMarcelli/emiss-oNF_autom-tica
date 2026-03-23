# Contrato de Entrada (SEFAZ-MT)

## 1. Visão Geral da UF
* **Arquivo do Motor:** `servicos_sefaz_mt.py`
* **Função Principal:** `emitir`
* **Resumo:** Lida com a API de geração transacional de MT. Possui arquitetura rígida onde os `acrescimos` precisam vir estipulados de fora; não auto-calcula e se defende contra suposições do desenvolvedor com Hard Fail de PDFs 0kb corruptos (falsos positivos).

## 2. Contrato de `dados_emissao`

| Campo | Obrigatório? | Tipo | Formato / Regra |
|---|---|---|---|
| `ie_cnpj` | Opcional | `String` | MT aceita emissões desvinculadas por CNPJ ou nome sujo. |
| `receita_codigo` | **SIM** | `String` | Enviar a Descrição Exata Mapeada (ex: `FETHAB - ...`) conforme enum ou portal oficial. |
| `valor` | **SIM** | `Float` | Principal, ex: `200.00`. |
| `referencia` | **SIM** | `String` | `MM/AAAA`. |
| `data_vencimento` | **SIM** | `String` | `DD/MM/AAAA`. Não insere defaults ocultos. Omitir invoca erro de validação iminente do motor. |
| `data_pagamento` | Opcional | `String` | Assimila-se do vencimento se null. |
| `acrescimos` | **Misto** (Apenas se vencida) | `Dict` / `Array` | Ver sub-seção *Acréscimos* abaixo para evitar rejeição fiscal na emissão regressa. |

## 3. Regras Específicas da UF
* **Vencida / Não Vencida:** MT abraça a modalidade Vencida, desde que perfeitamente parametrizada pelo originador no objeto `acrescimos`.
* **Cálculo Automático/Manual:** MANUAL (Payload-Bound). O portal SEFAZ-MT não calculará a penalidade nativamente no motor de emissões; ele espera ser munido do montante exato provido pelo dev.
* **Objeto `Acrescimos` Exigível:** Ao requisitar data de vencimento no passado (< Hoje), o dicionário python obrigatoriamente deve conter a chave de objeto detalhada:
   `"acrescimos": [ {"tipo": "juros", "valor": 5.50}, {"tipo": "multa", "valor": 1.25} ]`
* **Código vs Nome do Tributo:** Busca textual/ID no portal MT (Ex: `8938`).
* **Regra Anti Falso-Positivo PDF 0kb:** O módulo implementou proteção severa interceptando respostas HTML cruas mascaradas de "application/pdf".

## 4. Falhas Esperadas
| O que Bloqueia | Mensagem/Tipagem | Limitação Técnica |
|---|---|---|
| Exigência de Multa Vencida | `Guia vencida detectada, mas campo "acrescimos"... não foi informado` | Submeter data vencida omitindo o array de multas tranca sumariamente a SEFAZ de emitir o PDF oficial de forma lícita, estourando Exception do motor. |
| PDF Inválido/Corrompido | `Sucesso aparente, mas PDF corrompido (tamanho 0 ou sem Magic Number).` | Transação abortada protegendo a ponta da aplicação contra comprovantes defeituosos da SEFAZ MT. |

## 5. Exemplos

### 🟢 Payload Funcional (Vencido)
```python
dados_emissao = {
    "ie_cnpj": "12.345.678/0001-90",
    "receita_codigo": "IPVA",
    "valor": 105.50,
    "referencia": "12/2023",
    "data_vencimento": "15/01/2024",
    "data_pagamento": "23/04/2024",
    "acrescimos": [
        {"tipo": "juros", "valor": 15.0},
        {"tipo": "multa", "valor": 2.50}
    ]
}
```

### 🔴 Payload que Deve Falhar (Vencido e Sem Repasse de Taxas)
```python
dados_emissao = {
    "ie_cnpj": "12.345.678/0001-90",
    "receita_codigo": "IPVA",
    "valor": 105.50,
    "referencia": "12/2023",
    "data_vencimento": "15/01/2024" # Data do ano anterior!
}
```
**Motivo:** Se há vencimento passado e caduco sem instanciar `acrescimos` apontando "0.00" ou afins, o motor bloqueia a requisição avisando ao dev: *Guia vencida detectada, faltam encargos declarados no payload.*

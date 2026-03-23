# Contrato de Entrada (SEFAZ-SP)

## 1. Visão Geral da UF
* **Arquivo do Motor:** `servicos_sefaz_sp.py`
* **Função Principal:** `emitir`
* **Resumo:** Engancha-se no ambiente transacional DARE Avulso em São Paulo (Request/HTTP puro). Lida estritamente com requisições criptografadas de ViewState, bypass de captcha client-side e repasses de solvers robustos, e recálculo explícito via backend.

## 2. Contrato de `dados_emissao`

| Campo | Obrigatório? | Tipo | Formato / Regra |
|---|---|---|---|
| `ie_cnpj` | **SIM** | `String` | IE, CNPJ ou CPF formataçado/crú. Base essencial. |
| `receita_codigo` | **SIM** | `String` | Código exato de Geração. Exemplos clássicos: `"046-2"` ou `"1002"`. |
| `valor` | **SIM** | `Float` | Sem zero embutido. |
| `referencia` | **SIM** | `String` | Padrão SP para apuração mensal. |
| `data_vencimento` | **SIM** | `String` | Foco estrutural nos recálculos gerados pelo portal de SP. |
| `data_pagamento` | Opcional | `String` | O DTO da Sefaz consumirá a data de repasse ao emitir. |

## 3. Regras Específicas da UF
* **Vencida / Não Vencida:** A automação atende perfeitamente ambas casuísticas, extraindo o montante devido na hora H.
* **Cálculo Automático/Manual:** AUTO-AÇÃO NATIVA ESTADUAL. O SP ignora hardcodes de juros via python e possui acionamento na Etapa 3A. A automação envia a requisição `/btnCalcular_Click/` informando as datas antigas, mastiga a resposta e realimenta imperativamente o próprio payload contendo o juros oficial (`valorJuros`, `valorMulta` e `valorTotal`).
* **Código vs Nome do Tributo:** SP usa Código com dígito e base. Não aceite preenchimentos nomeados.
* **Resolver de Captcha Oficial:** A automação foi polida para expurgar bloqueios operacionais ao máximo. Todavia, reCAPTCHA hostil estourará falha prematura caso não detecte o solver terceirizado via Variáveis de Ambiente. `TWOCAPTCHA_API_KEY` tem que existir no `.env`.

## 4. Falhas Esperadas
| O que Bloqueia | Mensagem/Tipagem | Limitação Técnica |
|---|---|---|
| Solver Captcha Ausente | `Fail-Fast: Variável TWOCAPTCHA_API_KEY ausente... Solver é obrigatório` | N/A (Regra anti-bloqueio configuracional humana). |
| Resposta do Auto-Recálculo Negativa | `O portal SP rejeitou o recálculo dos juros/multa para a data` | Quando mandado faturas com dezenas de anos vencidos e IE suspensa, a Sefaz rejeita o cálculo do payload 3A bloqueantemente e impede a emissão crua para evitar fraude contábil. |

## 5. Exemplos

### 🟢 Payload Funcional (Gerará Juros Dinamicamente em SP e fará Fetch) 
```python
dados_emissao = {
    "ie_cnpj": "51.789.601/0001-66",
    "receita_codigo": "046-2",
    "valor": 100.00,
    "referencia": "10/2023",
    "data_vencimento": "24/03/2026", # Pagamento atualizando o atraso retroativo
}
```

### 🔴 Payload que Deve Falhar
```python
dados_emissao = {
    "ie_cnpj": "51789601000166",
    "receita_codigo": "ISS DA CAPITAL", # SP recusa strings, precisa do codigo DARE.
    "valor": 100.00
    # Omisso em vencer/refs
}
```
**Motivo:** Além da falha do identificador da receita, sem Vencimento e Referência os fluxos essenciais transacionais de `/btnCalcular_Click/` capotarão via JSON incompleto.

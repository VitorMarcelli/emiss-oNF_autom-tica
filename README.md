# Emissão de Guias ICMS — Motor por UF

Automatiza a emissão de guias de ICMS (DARE, DAE, GR-PR, DAR-1) nos portais
estaduais, gerando PDFs prontos para pagamento.

## Pré-requisitos

- **Windows 10/11**
- **Python 3.10+** instalado e disponível no terminal (`python --version`)
  - Na instalação do Python, marque a caixa **"Add Python to PATH"**

## Instalação

Execute uma única vez no terminal (CMD ou PowerShell):

```
setup_windows.bat
```

O script irá:
1. Criar um ambiente virtual isolado (`.venv`)
2. Instalar as dependências
3. Baixar o navegador Chromium headless (usado internamente)
4. Validar que tudo está pronto

Se tudo der certo, você verá `[SUCESSO] Ambiente configurado corretamente!`

> **Redes corporativas:** Se o pip ou o Playwright falharem por proxy/firewall,
> configure as variáveis `HTTP_PROXY` e `HTTPS_PROXY` no sistema ou execute
> a instalação em uma rede sem restrições.

## Como usar

### Listar receitas disponíveis de uma UF

```
run_windows.bat SP --listar-receitas
run_windows.bat MT --listar-receitas
run_windows.bat MG --listar-receitas
run_windows.bat PR --listar-receitas
run_windows.bat GO --listar-receitas
```

O resultado é salvo em `mappings/<UF>.json` para consulta e cache.

### Emitir uma guia

```
run_windows.bat SP --receita 4601 --cnpj 12345678000199 --valor 100,00 --mes 03 --ano 2026
run_windows.bat MT --receita 1112 --ie 133201040 --valor 100,00 --mes 03 --ano 2026
run_windows.bat MG --receita 121-7 --cnpj 06230790400078 --valor 100,00 --mes 03 --ano 2026
run_windows.bat PR --receita 1015 --ie 9023307399 --valor 10,00 --mes 03 --ano 2026
run_windows.bat GO --receita 121 --ie 10123456-7 --valor 100,00 --mes 03 --ano 2026
```

### Usar cache antes de emitir

```
run_windows.bat SP --usar-cache --receita 4601 --cnpj 12345678000199 --valor 100,00 --mes 03 --ano 2026
```

O flag `--usar-cache` valida o código da receita contra o arquivo `mappings/<UF>.json`
antes de acessar o portal.

### Parâmetros disponíveis

| Parâmetro | Descrição |
|-----------|-----------|
| `UF` | Sigla do estado (SP, MT, MG, PR, GO) |
| `--listar-receitas` | Gera o catálogo de receitas do portal |
| `--usar-cache` | Valida receita contra cache local |
| `--receita` | Código da receita/tributo |
| `--cnpj` | CNPJ do contribuinte |
| `--ie` | Inscrição Estadual |
| `--valor` | Valor da guia (ex: 100,00) |
| `--mes` | Mês de referência (MM) |
| `--ano` | Ano de referência (AAAA) |
| `--juros` | Valor de juros (quando aplicável) |
| `--multa` | Valor de multa (quando aplicável) |
| `--correcao` | Correção monetária (quando aplicável) |
| `--pdf-dir` | Diretório personalizado para os PDFs |

## Onde ficam os resultados

| Local | Conteúdo |
|-------|----------|
| `pdfs/<UF>/` | PDFs das guias emitidas |
| `mappings/<UF>.json` | Cache das receitas disponíveis |
| `debug/` | Snapshots de erro para diagnóstico |

## Se der erro

1. Copie a linha de erro completa exibida no terminal
2. Verifique se existe arquivo de debug em `debug/<UF>_ultima_resposta.html`
3. Envie ambos para análise

Erro típico:
```
etapa: <nome> | motivo: <causa> | detalhe: <informação extra>
```

## Limitações conhecidas

| UF | Observação |
|----|------------|
| PR | Exige reCaptcha Enterprise — fluxo suportado via variável de ambiente TWOCAPTCHA_API_KEY |
| GO | Fluxo via Playwright (PrimeFaces) |
| SP | Bypass nativo do captcha implementado (funciona automaticamente) |
| MT, MG | Portais públicos sem bloqueio observado |

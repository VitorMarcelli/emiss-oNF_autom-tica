<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Playwright-Automation-2EAD33?style=for-the-badge&logo=playwright&logoColor=white" />
  <img src="https://img.shields.io/badge/Status-Produção-brightgreen?style=for-the-badge" />
  <img src="https://img.shields.io/badge/UFs-7_Estados-blue?style=for-the-badge" />
</p>

<h1 align="center">⚡ Emissão Automática de Guias ICMS</h1>

<p align="center">
  <strong>Motor inteligente que automatiza a emissão de guias de recolhimento de ICMS diretamente nos portais das Secretarias da Fazenda estaduais — gerando PDFs prontos para pagamento em segundos.</strong>
</p>

<p align="center">
  <em>Transformando horas de trabalho manual repetitivo em uma única linha de comando.</em>
</p>

---

## 🎯 O Problema

Profissionais de contabilidade e departamentos fiscais gastam **horas por mês** acessando manualmente os portais de cada Secretaria da Fazenda para emitir guias de ICMS. Cada estado tem um portal diferente, com formulários distintos, fluxos complexos e — muitas vezes — bloqueios de CAPTCHA que tornam o processo ainda mais demorado.

**E se um único comando pudesse fazer tudo isso por você?**

## 💡 A Solução

Este motor automatiza **todo o fluxo** de emissão: desde a autenticação no portal, preenchimento de formulários, resolução inteligente de CAPTCHAs, até o download do PDF final — tudo 100% programático.

```
run_windows.bat SP --receita 4601 --cnpj 12345678000199 --valor 100,00 --mes 03 --ano 2026
```

**Uma linha. Um PDF. Pronto para pagamento. ✅**

---

## 🗺️ Estados Suportados

| UF | Guia | Método | CAPTCHA | Status |
|----|------|--------|---------|--------|
| 🟢 **São Paulo** | DARE-SP | HTTP Direto | Bypass nativo | ✅ Produção |
| 🟢 **Minas Gerais** | DAE-MG | HTTP Direto | Sem bloqueio | ✅ Produção |
| 🟢 **Mato Grosso** | DAR-1-MT | HTTP Direto | Sem bloqueio | ✅ Produção |
| 🟢 **Goiás** | DARE-GO | Playwright | PrimeFaces bypass | ✅ Produção |
| 🟢 **Paraná** | GR-PR | Playwright + HTTP | reCAPTCHA Enterprise | ✅ Produção |
| 🟢 **Mato Grosso do Sul** | DARE-MS | Playwright | Automação direta | ✅ Produção |
| 🔵 **Outros** | — | — | — | Em planejamento |

---

## ⚙️ Arquitetura

```
emissao_nf_junes/
│
├── ufs/                        # 🧠 Módulos por estado
│   ├── servicos_sefaz_sp.py    #     São Paulo
│   ├── servicos_sefaz_mg.py    #     Minas Gerais
│   ├── servicos_sefaz_mt.py    #     Mato Grosso
│   ├── servicos_sefaz_go.py    #     Goiás
│   ├── servicos_sefaz_pr.py    #     Paraná
│   ├── servicos_sefaz_ms.py    #     Mato Grosso do Sul
│   ├── captcha_utils.py        #     Utilitários de CAPTCHA
│   └── solver_2captcha.py      #     Integração 2Captcha
│
├── docs/                       # 📚 Documentação técnica
├── run_windows.bat             # 🚀 Script de execução
├── setup_windows.bat           # 📦 Instalação automatizada
└── requirements.txt            # 📋 Dependências Python
```

Cada módulo em `ufs/` é **auto-contido** e segue uma interface padronizada:

```python
# Todos os módulos exportam a mesma assinatura
sucesso, resultado = emitir(session, dados_emissao, path_pdf)
# → True,  {"pdf_path": "...", "pdf_filename": "..."}
# → False, "etapa: X | motivo: Y | detalhe: Z"
```

---

## 🚀 Início Rápido

### 1. Pré-requisitos

- **Windows 10/11**
- **Python 3.10+** (com "Add Python to PATH" marcado na instalação)

### 2. Instalação

```bash
setup_windows.bat
```

O script configura tudo automaticamente: ambiente virtual, dependências e navegador headless.

### 3. Uso

**Listar receitas disponíveis:**

```bash
run_windows.bat SP --listar-receitas
run_windows.bat GO --listar-receitas
```

**Emitir uma guia:**

```bash
run_windows.bat SP --receita 4601 --cnpj 12345678000199 --valor 100,00 --mes 03 --ano 2026
run_windows.bat GO --receita 108 --ie 10410432-5 --valor 100,00 --mes 03 --ano 2026
```

---

## 📋 Parâmetros

| Parâmetro | Descrição |
|-----------|-----------|
| `UF` | Sigla do estado (SP, MT, MG, PR, GO, MS) |
| `--listar-receitas` | Lista todas as receitas disponíveis no portal |
| `--usar-cache` | Valida a receita contra cache local antes de acessar o portal |
| `--receita` | Código da receita/tributo |
| `--cnpj` | CNPJ do contribuinte |
| `--ie` | Inscrição Estadual |
| `--valor` | Valor da guia (ex: 100,00) |
| `--mes` / `--ano` | Período de referência |
| `--juros` / `--multa` / `--correcao` | Acréscimos (quando aplicável) |
| `--pdf-dir` | Diretório personalizado para salvar os PDFs |

---

## 🔐 Sobre CAPTCHAs

O motor utiliza diferentes estratégias por estado:

- **SP** — Bypass nativo via endpoint secundário de validação
- **GO** — Contorno via automação Playwright em componentes PrimeFaces
- **PR** — Integração com serviço 2Captcha para reCAPTCHA Enterprise (requer variável `TWOCAPTCHA_API_KEY`)
- **MT, MG, MS** — Portais sem bloqueio por CAPTCHA

---

## 🤝 Contribuição

Contribuições são bem-vindas! Se você trabalha com automação fiscal e deseja adicionar suporte a novos estados, sinta-se à vontade para abrir uma PR.

---

## ⚖️ Aviso Legal

Este projeto é uma **ferramenta de automação para fins legítimos** — projetada para agilizar o trabalho de profissionais de contabilidade e departamentos fiscais que já realizam essas operações manualmente. O uso deve estar em conformidade com os termos de uso dos portais estaduais e com a legislação vigente.

---

<p align="center">
  Feito com ☕ e Python por <strong>Vitor Marcelli</strong>
</p>

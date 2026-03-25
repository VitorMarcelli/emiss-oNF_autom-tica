"""
Módulo — Emissão de DARE (ICMS) para Goiás (GO).
Portal: https://portal.sefaz.go.gov.br/

⚠️ LIMITAÇÃO IMPORTANTE:
  O DARE de ICMS mensal apurado em Goiás é gerado EXCLUSIVAMENTE via
  "DARE pré-preenchido" em ACESSO RESTRITO no portal da SEFAZ-GO.
  Desde outubro/2025, o DARE pré-preenchido é o único canal para emissão
  de ICMS mensal apurado via EFD. O acesso exige login (CPF/CNPJ + senha
  ou certificado digital) no portal https://portal.sefaz.go.gov.br/portalsefaz-apps

  Para OUTROS tipos de ICMS (ICMS-ST, Avulso, Auto de Infração), existe
  a possibilidade via portal público, mas também podem exigir login.

CONTRATO DE ENTRADA (dados_emissao):
  Principais campos exigidos para emissão:
  - ie_cnpj: Inscrição Estadual ou CNPJ.
  - receita_codigo: Código da receita (ex: "108" para Normal).
  - valor: Valor do documento (> 0).
  - referencia: Período base da apuração no formato MM/AAAA.
  - tipo_referencia: OBRIGATÓRIO (valores aceitos: "diaria" ou "complementar").
       * "diaria": seleciona o Dia atual como Detalhe de Apuração.
       * "complementar": seleciona automaticamente a opção 'Complementar' no Detalhe de Apuração.

Retorno padronizado:
  Sucesso: True, {"mensagem": "ok", "pdf_path": "...", "pdf_filename": "..."}
  Erro:    False, "etapa: <nome> | motivo: <causa> | detalhe: <curto>"
"""

import logging
import html as html_lib
import re
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Tuple, Union
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from .captcha_utils import checar_captcha_e_retornar
except ImportError:
    from captcha_utils import checar_captcha_e_retornar

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("sefaz_go")

# ---------------------------------------------------------------------------
# Constantes do portal
# ---------------------------------------------------------------------------
# URL do portal de aplicações (acesso restrito)
PORTAL_APPS_URL = "https://portal.sefaz.go.gov.br/portalsefaz-apps"

# URL para DARE avulso/público (quando disponível)
DARE_PUBLICO_URL = "https://portal.sefaz.go.gov.br/portalsefaz-apps/pagamento/dare/emissao"
ARR_ENTRADA_URL = (
    "https://arr.economia.go.gov.br/arr-www/view/entradaContribuinte.jsf"
    "?protocoloAtendeGoias=c53b20dccb0aef8e7b2e76f471700a14"
)

TIMEOUT = 60

HEADERS_NAV: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

HEADERS_AJAX: dict[str, str] = {
    **HEADERS_NAV,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

ResultadoEmissao = Tuple[bool, Union[dict, str]]

MAPEAMENTO_RECEITAS_GO = {
    "108": "ICMS - NORMAL",
    "116": "SUBSTITUIÇÃO TRIBUTÁRIA DIRETRIZES",
    "124": "SUBSTITUIÇÃO TRIBUTÁRIA LEASING",
    "132": "SUBSTITUIÇÃO TRIBUTÁRIA PEÇAS",
    "140": "SUBSTITUIÇÃO TRIBUTÁRIA DIVERSOS",
    "311": "SUBSTITUIÇÃO TRIBUTÁRIA",
    "202": "PAGAMENTO ANTECIPADO - SAÍDA",
    "167": "IMPORTAÇÃO",
}

# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------
def _normalizar_erro(etapa: str, motivo: str, detalhe: str = "") -> str:
    msg = f"etapa: {etapa} | motivo: {motivo}"
    if detalhe:
        msg += f" | detalhe: {detalhe}"
    return msg


# Padrões que indicam erro de VALIDAÇÃO do portal (contribuinte inválido)
_PADROES_ERRO_VALIDACAO = [
    "contribuinte não encontrado",
    "contribuinte nao encontrado",
    "ie/cnpj inválido",
    "cnpj inválido",
    "ie inválida",
    "dados não localizados",
    "dados nao localizados",
    "inscrição não encontrad",
    "inscrição inativ",
    "inscrição invalid",
    "inscricao nao encontrad",
    "inscricao inativ",
    "inscricao invalid",
    "não foi possível identificar",
    "nao foi possivel identificar",
]

# Indicadores de CAPTCHA para detecção no Playwright
_INDICADORES_CAPTCHA_PW = [
    "captcha", "recaptcha", "g-recaptcha", "hcaptcha",
    "data-sitekey", "recaptcha/api", "js.hcaptcha.com",
]


def _erro_e_de_validacao(msg_erro: str) -> bool:
    """Retorna True se a mensagem de erro indica falha de validação do portal.
    Erros de validação NÃO devem acionar fallback HTTP (que causa CAPTCHA).
    """
    texto = msg_erro.lower() if isinstance(msg_erro, str) else str(msg_erro).lower()
    return any(padrao in texto for padrao in _PADROES_ERRO_VALIDACAO)


def _capturar_csrf(html: str, etapa: str = "capturar_csrf") -> str:
    """Tenta extrair token CSRF/anti-forgery do HTML."""
    soup = BeautifulSoup(html, "html.parser")

    for name_attr in ["__RequestVerificationToken", "csrf_token",
                       "_token", "csrfmiddlewaretoken"]:
        tag = soup.find("input", {"name": name_attr})
        if tag and tag.get("value"):
            return tag["value"]

    meta = soup.find("meta", {"name": re.compile(r"csrf", re.I)})
    if meta and meta.get("content"):
        return meta["content"]

    raise ValueError(
        _normalizar_erro(etapa, "token CSRF não encontrado no HTML")
    )


def _validar_entradas(
    ie_cnpj: str,
    codigo_receita: str,
    referencia: str,
    valor: float,
    tipo_referencia: str,
    data_pagamento: str = "",
) -> None:
    if not ie_cnpj or not ie_cnpj.strip():
        raise ValueError(
            _normalizar_erro("validar_entrada", "IE/CNPJ ausente")
        )
    if not codigo_receita:
        raise ValueError(
            _normalizar_erro("validar_entrada", "código da receita ausente")
        )
    if not referencia or not re.match(r"^\d{2}/\d{4}$", referencia):
        raise ValueError(
            _normalizar_erro(
                "validar_entrada",
                "referência inválida (esperado MM/AAAA)",
            )
        )
    if not tipo_referencia or tipo_referencia.lower() not in ["diaria", "complementar"]:
        raise ValueError(
            _normalizar_erro(
                "validar_entrada",
                "tipo_referencia inválido ou ausente (esperado 'diaria' ou 'complementar')",
            )
        )
    if valor <= 0:
        raise ValueError(
            _normalizar_erro("validar_entrada", "valor deve ser > 0")
        )

    # ── Validação de data_pagamento (regra GO: não pode ser inferior à data atual) ──
    if data_pagamento:
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", data_pagamento):
            raise ValueError(
                _normalizar_erro(
                    "validar_entrada",
                    "data_pagamento inválida",
                    "formato esperado: DD/MM/AAAA",
                )
            )
        try:
            dt_pagamento = datetime.strptime(data_pagamento, "%d/%m/%Y").date()
        except ValueError:
            raise ValueError(
                _normalizar_erro(
                    "validar_entrada",
                    "data_pagamento inválida",
                    f"não foi possível interpretar '{data_pagamento}' como DD/MM/AAAA",
                )
            )
        hoje = datetime.now().date()
        if dt_pagamento < hoje:
            raise ValueError(
                _normalizar_erro(
                    "validar_entrada",
                    "data_pagamento inválida",
                    f"no estado de GO, a data de pagamento não pode ser inferior à data atual "
                    f"(data_pagamento={data_pagamento}, hoje={hoje.strftime('%d/%m/%Y')})",
                )
            )


def _detectar_login_ou_captcha(resp: requests.Response) -> bool:
    """Verifica se o portal redirecionou para login ou exibe captcha."""
    url_lower = resp.url.lower()
    text_lower = resp.text[:2000].lower() if resp.text else ""

    sinais = [
        "login" in url_lower,
        "autenticacao" in url_lower,
        "captcha" in text_lower,
        "recaptcha" in text_lower,
        "g-recaptcha" in text_lower,
        "certificado digital" in text_lower,
    ]
    return any(sinais)


def _baixar_pdf(
    resp: requests.Response,
    pdf_path: str,
) -> Tuple[str, str]:
    content_type = resp.headers.get("Content-Type", "")
    if "application/pdf" not in content_type.lower():
        raise ValueError(
            _normalizar_erro(
                "baixar_pdf",
                "resposta não é PDF",
                f"content-type={content_type}",
            )
        )

    filename = None
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        match = re.search(r'filename="?([^";\r\n]+)"?', cd)
        if match:
            filename = match.group(1).strip()

    if not filename:
        agora = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"DARE_GO_{agora}.pdf"

    destino = Path(pdf_path)
    if destino.is_dir() or not str(destino).lower().endswith(".pdf"):
        destino = destino / filename
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resp.content)

    logger.info("PDF salvo em %s (%d bytes)", destino, len(resp.content))
    return str(destino), filename


def _emitir_via_playwright(
    ie_cnpj: str,
    pdf_path: str,
    codigo_receita: str,
    referencia: str,
    tipo_referencia: str,
    data_vencimento: str,
    valor: float,
    data_pagamento: str = "",
    detalhe_receita: str = "",
    contribuinte_modo: str = "inscrito",
    nome_razao_social: str = "",
    cep: str = "",
    logradouro: str = "",
    numero: str = "",
    complemento: str = "",
    bairro: str = "",
) -> ResultadoEmissao:
    """Tenta emissão via robô Playwright no ARR para contornar bloqueios do PrimeFaces."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        return False, _normalizar_erro("carregar_playwright", "Playwright não instalado no ambiente (pip install playwright)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(
            user_agent=HEADERS_NAV["User-Agent"],
            accept_downloads=True,
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()

        try:
            logger.info("Playwright: Acessando ARR_ENTRADA_URL")
            page.goto(ARR_ENTRADA_URL, timeout=60000)

            # --- Detecção de CAPTCHA no fluxo principal ---
            html_pagina = page.content().lower()
            for indicador in _INDICADORES_CAPTCHA_PW:
                if indicador in html_pagina:
                    debug_dir = Path(__file__).parent / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    snapshot_path = debug_dir / "GO_captcha_detectado.html"
                    snapshot_path.write_text(
                        page.content()[:5000], encoding="utf-8"
                    )
                    logger.warning(
                        "CAPTCHA detectado no fluxo principal GO (indicador=%s)",
                        indicador,
                    )
                    return False, _normalizar_erro(
                        "acesso_portal",
                        "captcha detectado",
                        f"bloqueio anti-bot no portal GO | snapshot: {snapshot_path}",
                    )


            texto_esperado = MAPEAMENTO_RECEITAS_GO.get(codigo_receita, "ICMS")

            logger.info("Playwright: Procurando receita '%s'", texto_esperado)
            link_locator = page.locator(f"a:has(span:has-text('{texto_esperado}'))").first
            
            with context.expect_page() as new_page_info:
                link_locator.click(timeout=15000)
            
            page = new_page_info.value
            page.wait_for_selector("form#frmEmissao", timeout=30000)
            logger.info("Playwright: Formulário carregado")

            doc = re.sub(r"[.\-/\s]", "", ie_cnpj or "")
            modo_nao_inscrito = contribuinte_modo.strip().lower() in ("nao_inscrito", "nao-inscrito", "naoinscrito")

            if modo_nao_inscrito:
                page.locator("text=/Não Inscrito no Cadastro/i").first.click()
                page.wait_for_selector("input[id$='radioCPFCNPJ:0']", timeout=10000)
                if len(doc) == 11:
                    page.locator("label[for$='radioCPFCNPJ:0']").click()
                else:
                    page.locator("label[for$='radioCPFCNPJ:1']").click()
                
                # Aguardar re-renderização do PrimeFaces
                page.wait_for_timeout(3000)
                
                # Injetar via JS para não brigar com a máscara
                page.evaluate("""(val) => {
                    const el = document.querySelector("input[id$='txtNumeroCPFCNPJ']");
                    if (el) {
                        el.value = val;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                        el.dispatchEvent(new Event('blur', {bubbles:true}));
                    }
                }""", doc)
                page.wait_for_timeout(2000)
                
                page.locator("input[id$='txtNomeRazaoSocial']").fill(nome_razao_social)
                page.locator("input[id$='txtCEP']").fill(cep)
                page.locator("input[id$='txtLogradouro']").fill(logradouro)
                page.locator("input[id$='txtNumero']").fill(numero)
                if complemento:
                    page.locator("input[id$='txtComplemento']").fill(complemento)
                page.locator("input[id$='txtBairro']").fill(bairro)
            else:
                page.locator("text=/Inscrito no Cadastro/i").first.click(timeout=10000)
                page.wait_for_timeout(1000)
                doc_input = page.locator("input[id$='txtInscricao']")
                doc_input.wait_for(state="visible", timeout=10000)
                doc_input.fill(doc)
                doc_input.dispatch_event("blur")
                page.wait_for_timeout(2000)
            
            try:
                page.wait_for_selector("div.ui-dialog-content:has-text('Carregando...')", state="hidden", timeout=5000)
            except PwTimeout:
                pass

            # ── Passo 2: Continuar após contribuinte ────────────────────
            page.locator("button[id*='btnContinueBaixo']").click()
            page.wait_for_timeout(3000)

            # Verificar erros de validação do contribuinte
            erros = page.locator(".ui-growl-message, .ui-messages-error").all_inner_texts()
            if erros:
                msg_erro = " ".join(erros).strip()
                if msg_erro:
                    return False, _normalizar_erro("identificar_contribuinte", "erro do portal (validação)", msg_erro[:200])

            # ── Passo 3: Selecionar receita na árvore PrimeFaces ────────
            # A árvore tem 3+ níveis com filhos em display:none.
            # Fluxo: expandir nós-pai clicando no ícone-triângulo,
            # depois selecionar o nó-folha (apuração: "0 - Diário").
            #
            # Estrutura:
            #   node_0: "ICMS - NORMAL"
            #     node_0_0: "1 - ICMS - IMPOSTO CIRCUL..."
            #       node_0_0_0: "108 - NORMAL"
            #         node_0_0_0_0: "0 - Diário"  ← folha a selecionar
            logger.info("Playwright: Expandindo árvore de receitas")
            try:
                page.wait_for_selector(".ui-tree", timeout=10000)
                page.wait_for_timeout(500)

                # Expansão via JavaScript direto — contorna problemas de seletores
                # PrimeFaces no Playwright. Abre todos os nós necessários via DOM nativo.
                logger.info("Playwright: Expandindo árvore via JavaScript (3 níveis)")
                
                expand_result = page.evaluate("""() => {
                    const results = [];
                    
                    function expandNode(nodeId) {
                        const node = document.getElementById(nodeId);
                        if (!node) return "NOT_FOUND: " + nodeId;
                        
                        // Forçar visibilidade do próprio nó
                        node.style.display = '';
                        
                        // Encontrar o toggler (span com classe que contém 'triangle' ou 'toggler')
                        const contentDiv = node.querySelector(':scope > div, :scope > .ui-treenode-content');
                        if (!contentDiv) return "NO_CONTENT_DIV: " + nodeId;
                        
                        const toggler = contentDiv.querySelector(
                            '.ui-tree-toggler, .ui-tree-icon, [class*="triangle"], [class*="toggler"]'
                        );
                        
                        if (toggler) {
                            // Simular clique real com todos os handlers
                            toggler.click();
                        }
                        
                        // Forçar visibilidade dos filhos (ul container)
                        const childContainer = node.querySelector(':scope > ul, :scope > .ui-treenode-children');
                        if (childContainer) {
                            childContainer.style.display = 'block';
                        }
                        
                        return "OK: " + nodeId;
                    }
                    
                    // Prefixo do ID da árvore
                    const prefix = 'frmEmissao:apReceitaEstadual:treeReceita_node_';
                    
                    // Expandir nível 1: ICMS - NORMAL
                    results.push(expandNode(prefix + '0'));
                    
                    // Expandir nível 2: 1 - ICMS - IMPOSTO...
                    results.push(expandNode(prefix + '0_0'));
                    
                    // Expandir nível 3: 108 - NORMAL
                    results.push(expandNode(prefix + '0_0_0'));
                    
                    return results;
                }""")
                
                logger.info("Playwright: Resultado expansão JS: %s", expand_result)
                
                # Verificar se algum nó não foi encontrado
                for r in (expand_result or []):
                    if "NOT_FOUND" in str(r):
                        return False, _normalizar_erro(
                            "selecionar_receita",
                            "nó da árvore não encontrado no DOM",
                            f"Resultado da expansão JS: {expand_result}"
                        )
                
                page.wait_for_timeout(2000)

                # Nível 4: selecionar folha baseada no tipo de referência
                # Mapeamento explícito conforme árvore real do portal:
                #   diaria       -> "0 - Diário"
                #   complementar -> "400 - Complementar"
                if tipo_referencia.lower() == "diaria":
                    texto_folha_esperado = "0 - Di"      # match parcial seguro
                    texto_folha_log = "0 - Diário"
                else:
                    texto_folha_esperado = "400 - Complementar"
                    texto_folha_log = "400 - Complementar"
                
                logger.info("Playwright: Procurando nó-folha '%s' na árvore", texto_folha_log)
                
                # Buscar dentro do pai node_0_0_0 (108 - NORMAL)
                pai_locator = page.locator("#frmEmissao\\:apReceitaEstadual\\:treeReceita_node_0_0_0")
                leaf = pai_locator.locator(".ui-treenode-label, .ui-tree-node-label", has_text=re.compile(re.escape(texto_folha_esperado), re.IGNORECASE)).first
                
                if leaf.count() == 0:
                    # Busca global como segunda tentativa (sem fallback arbitrário)
                    logger.warning("Nó '%s' não encontrado no pai direto, buscando em toda a árvore", texto_folha_log)
                    leaf = page.locator(".ui-treenode-label, .ui-tree-node-label", has_text=re.compile(re.escape(texto_folha_esperado), re.IGNORECASE)).first
                
                if leaf.count() == 0:
                    return False, _normalizar_erro(
                        "selecionar_receita",
                        "nó obrigatório não encontrado na árvore",
                        f"O nó '{texto_folha_log}' não foi localizado na árvore de receitas do portal GO. Verifique se a receita/IE possui esse tipo de apuração."
                    )
                
                leaf.click(timeout=10000)
                page.wait_for_timeout(1000)

                # ── Readback: confirmar visualmente o que foi selecionado ──
                texto_selecionado = leaf.inner_text()
                logger.info("Playwright: Nó efetivamente selecionado na árvore = '%s'", texto_selecionado)
                
                if texto_folha_esperado.lower() not in texto_selecionado.lower():
                    return False, _normalizar_erro(
                        "selecionar_receita",
                        "nó selecionado divergente do esperado",
                        f"Esperado: '{texto_folha_log}' | Selecionado: '{texto_selecionado}'"
                    )

                logger.info("Playwright: Receita '%s' confirmada na árvore. Clicando Continue.", texto_selecionado)
                page.locator("button[id*='btnContinueBaixo']").click()
                page.wait_for_timeout(3000)

            except (PwTimeout, Exception) as exc:
                logger.error("Playwright: Falha FATAL na seleção da árvore de receitas: %s", str(exc)[:200])
                return False, _normalizar_erro("selecionar_receita", "falha ao selecionar receita na árvore do portal", str(exc)[:200])

            # ── Passo 5: Formulário financeiro ───────────────────────────
            # Agora o portal deve mostrar os campos: Mês, Ano, Data Vencimento,
            # Data Pagamento, Valor Original, etc.
            alvo = page.locator("input[id$='txtValorOriginal'], [id$='cdDataVencimento_input'], .ui-growl-message, .ui-messages-error")
            alvo.first.wait_for(timeout=20000)

            # Checar erros tardios
            erros = page.locator(".ui-growl-message, .ui-messages-error").all_inner_texts()
            if erros:
                msg_erro = " ".join(erros).strip()
                if msg_erro:
                    return False, _normalizar_erro("preencher_financeiro", "erro do portal", msg_erro[:200])

            logger.info("Playwright: Preenchendo valores financeiros")

            mes, ano = "", ""
            if referencia and "/" in referencia:
                mes, ano = referencia.split("/", 1)
            valor_str = f"{valor:.2f}".replace(".", ",")

            # Mês — PrimeFaces selectOneMenu (hidden <select> + AJAX)
            # Aguardar o formulário terminar de renderizar
            page.wait_for_timeout(2000)
            page.locator("[id$='cbMes_input'], [id$='cbMes_label']").first.wait_for(state="visible", timeout=15000)

            # Mapeamento de meses para texto do PrimeFaces
            meses_pt = {
                "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
                "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
                "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
            }
            mes_texto = meses_pt.get(mes, "Janeiro")
            
            def pf_set_value(id_suffix, wanted_text):
                res = page.evaluate(f"""(args) => {{
                    const idSuffix = args.suffix;
                    const wantedValue = args.text;
                    const panels = document.querySelectorAll('.ui-selectonemenu-panel');
                    let targetPanel = null;
                    for(let i=0; i<panels.length; i++) {{
                        if (panels[i].id && panels[i].id.indexOf(idSuffix) > -1) {{
                            targetPanel = panels[i];
                            break;
                        }}
                    }}
                    if (!targetPanel) return "Panel not found for " + idSuffix;
                    const lis = targetPanel.querySelectorAll('li.ui-selectonemenu-item');
                    let targetLi = null;
                    for(let i=0; i<lis.length; i++) {{
                        if (lis[i].getAttribute('data-value') === wantedValue || 
                            lis[i].getAttribute('data-label') === wantedValue || 
                            lis[i].innerText.includes(wantedValue)) {{
                            targetLi = lis[i];
                            break;
                        }}
                    }}
                    if (!targetLi) return "LI not found for " + wantedValue;
                    
                    let widget = null;
                    for (let k in window) {{
                        if (k.startsWith('widget_') && window[k] && window[k].id && window[k].id.indexOf(idSuffix) > -1) {{
                            widget = window[k];
                            break;
                        }}
                    }}
                    
                    if (widget && typeof widget.selectItem === 'function') {{
                        widget.selectItem( jQuery(targetLi) );
                        return "OK";
                    }} else {{
                        const evt = new MouseEvent('click', {{view: window, bubbles: true, cancelable: true}});
                        targetLi.dispatchEvent(evt);
                        return "OK";
                    }}
                }}""", {"suffix": id_suffix, "text": wanted_text})
                
                if res != "OK":
                    logger.warning("Playwright: Falha set %s='%s' via widget API: %s", id_suffix, wanted_text, res)
                return res == "OK"

            # Preencher Mês e Ano 
            logger.info("Playwright: Selecionando Mês=%s, Ano=%s", mes_texto, ano)
            pf_set_value("cbMes", mes_texto)
            pf_set_value("txtAno", ano)

            page.wait_for_timeout(1000)

            # Preencher Detalhe de Apuração — OBRIGATÓRIO para ambos os tipos
            if tipo_referencia.lower() == "diaria":
                try:
                    hoje = datetime.now()
                    dia_str = str(hoje.day)
                    
                    logger.info("Playwright: Tentando forçar Detalhe Apuração=%s (Diária)", dia_str)
                    pf_set_value("cbDetalheApuracao", dia_str)
                except Exception as e:
                    logger.warning("Playwright: Erro ao tentar detalhe apuração: %s", str(e))
            else:
                # Para Complementar: o nó '400 - Complementar' já foi selecionado
                # na árvore. O dropdown cbDetalheApuracao pode NÃO existir neste
                # formulário, pois a árvore já definiu o detalhe de apuração.
                dropdown_existe = page.evaluate("""() => {
                    const panels = document.querySelectorAll('.ui-selectonemenu-panel');
                    for (let p of panels) {
                        if (p.id && p.id.indexOf('cbDetalheApuracao') > -1) return true;
                    }
                    return false;
                }""")
                
                if dropdown_existe:
                    logger.info("Playwright: Dropdown cbDetalheApuracao encontrado — selecionando '400 - Complementar'.")
                    ok = pf_set_value("cbDetalheApuracao", "400")
                    if not ok:
                        ok = pf_set_value("cbDetalheApuracao", "Complementar")
                    if not ok:
                        ok = pf_set_value("cbDetalheApuracao", "400 - Complementar")
                    if ok:
                        page.wait_for_timeout(500)
                        label_sel = page.evaluate("""() => {
                            const l = document.querySelector("[id$='cbDetalheApuracao_label']");
                            return l ? l.innerText : "N/A";
                        }""")
                        logger.info("Playwright: Detalhe de Apuração efetivamente selecionado = '%s'", label_sel)
                    else:
                        logger.warning("Playwright: Não foi possível setar '400' no dropdown, mas a árvore já definiu complementar.")
                else:
                    logger.info("Playwright: Dropdown cbDetalheApuracao NÃO existe no formulário — árvore já definiu '400 - Complementar'. Prosseguindo.")

            # Preencher campos de data e valor (inputs simples PrimeFaces) via JavaScript
            page.evaluate("""(dados) => {
                function setField(idSuffix, valor) {
                    const el = document.querySelector("input[id$='" + idSuffix + "'], textarea[id$='" + idSuffix + "']");
                    if (el) {
                        el.value = valor;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                        el.dispatchEvent(new Event('blur', {bubbles:true}));
                    }
                }
                setField('cdDataVencimento_input', dados.venc);
                setField('cdDataPagamento_input', dados.pag);
                setField('txtValorOriginal', dados.valor);
                if (dados.compl) setField('txtInformacoesComplementares', dados.compl);
            }""", {
                "venc": data_vencimento,
                "pag": data_pagamento or data_vencimento,
                "valor": valor_str,
                "compl": detalhe_receita or "",
            })
            page.wait_for_timeout(1000)
            logger.info("Playwright: Datas e valor preenchidos")

            # ── Log de resumo pré-geração ──────────────────────────────
            logger.info("Playwright: RESUMO PRÉ-GERAÇÃO:")
            logger.info("  Receita = %s (código: %s)", MAPEAMENTO_RECEITAS_GO.get(codigo_receita, "N/A"), codigo_receita)
            logger.info("  Tipo Referência = %s", tipo_referencia)
            logger.info("  Referência = %s", referencia)
            logger.info("  Valor = %s", valor_str)
            logger.info("  Vencimento = %s | Pagamento = %s", data_vencimento, data_pagamento or data_vencimento)

            # Interceptar callback exibirDare para extrar o DARE, caso abra via popup interno
            page.evaluate("""() => {
                window.__capturedDare = null;
                if (typeof window.exibirDare === 'function') {
                    window.__origExibirDare = window.exibirDare;
                    window.exibirDare = function(numDare, consultaPeloId) {
                        window.__capturedDare = {numDare: numDare, consultaPeloId: consultaPeloId};
                        return window.__origExibirDare(numDare, consultaPeloId);
                    };
                }
            }""")

            # ── Passo 6: Gerar DARE ──────────────────────────────────────
            logger.info("Playwright: Clicando em Gerar DARE")
            page.locator("button[id*='btnGerarBaixo'], button[id*='btnGerar']").first.click()
            
            try:
                page.wait_for_function(
                    "() => window.__capturedDare || "
                    "Array.from(document.querySelectorAll('.ui-growl-message, .ui-messages-error')).some(e => e.offsetParent !== null && e.innerText.trim() !== '') || "
                    "Array.from(document.querySelectorAll('.ui-dialog-title')).some(e => e.offsetParent !== null && e.innerText.includes('Pr\\u00E9via'))",
                    timeout=60000
                )
                page.wait_for_timeout(1500)

                # Se abriu a dialog de Prévia (juros/multa)
                try:
                    previa = page.locator(".ui-dialog").filter(has_text=re.compile(r"Pr.via|DARE|Total do Documento", re.I))
                    previa.last.wait_for(state="visible", timeout=5000)
                    
                    if previa.count() > 0:
                        logger.info("Playwright: Dialog de Prévia (juros/multa) detectada. Confirmando a emissão...")
                        botoes = previa.first.locator("button")
                        btn_confirma = botoes.filter(has_text=re.compile(r"emitir|confirmar|sim|gerar|ok|imprimir", re.IGNORECASE))
                        
                        if btn_confirma.count() > 0:
                            logger.info("Playwright: Clicando no botão de confirmação: %s", btn_confirma.first.inner_text())
                            btn_confirma.first.click()
                        else:
                            logger.warning("Playwright: Botão explícito não encontrado. Tentando botão com classe ui-state-default...")
                            botoes_default = previa.first.locator("button.ui-state-default")
                            if botoes_default.count() > 0:
                                botoes_default.last.click()
                            else:
                                botoes.last.click()
                        
                        # Aguardar DARE novamente
                        page.wait_for_function(
                            "() => window.__capturedDare || "
                            "Array.from(document.querySelectorAll('.ui-growl-message, .ui-messages-error')).some(e => e.offsetParent !== null && e.innerText.trim() !== '')",
                            timeout=60000
                        )
                        page.wait_for_timeout(1000)
                except Exception as eval_err:
                    logger.error("Playwright: Erro ao manipular a dialog de Prévia: %s", repr(eval_err))
                    pass

            except PwTimeout:
                pass
            
            # Checar erros tardios
            erros_finais = page.locator(".ui-growl-message, .ui-messages-error").all_inner_texts()
            if erros_finais:
                msg_erro = " ".join(erros_finais).strip()
                if msg_erro and "sucesso" not in msg_erro.lower():
                    return False, _normalizar_erro("gerar_dare", "erro do portal", msg_erro[:200])

            # Verificar se o DARE foi capturado
            captured = page.evaluate("() => window.__capturedDare")
            if not captured or not captured.get("numDare"):
                # Capturar possível snapshot de erro
                page_content = page.content()
                Path("debug").mkdir(exist_ok=True)
                Path("debug/GO_falha_gerar_dare.html").write_text(page_content[:30000], encoding="utf-8")
                return False, _normalizar_erro("playwright_error", "timeout", "botão Gerar clicado mas exibirDare() não retornou numDare")

            numDare = captured["numDare"]
            dare_url = f"https://arr.economia.go.gov.br/arr-www/view/exibeDARE.jsf?codigo={numDare}"
            logger.info("Playwright: DARE capturado! Navegando para exibição: %s", dare_url)

            # O portal de GO abre uma página HTML com ícones de bancos para impressão.
            # Precisamos ir a essa página e clicar em um banco para iniciar o download do PDF.
            page.goto(dare_url, timeout=60000)
            
            logger.info("Playwright: Clicando em um banco na tela de exibição para capturar o PDF")
            with page.expect_download(timeout=120000) as download_info:
                page.locator("a[onclick*='FNC_ESCOLHE_BANCO']").first.click()
                
            download = download_info.value
            
            agora = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"DARE_GO_{agora}.pdf"
            destino = Path(pdf_path)
            if destino.is_dir() or not str(destino).lower().endswith(".pdf"):
                destino = destino / filename
            destino.parent.mkdir(parents=True, exist_ok=True)
            
            download.save_as(str(destino))
            logger.info("Playwright: PDF salvo em %s", destino)
            
            return True, {
                "mensagem": "ok",
                "pdf_path": str(destino),
                "pdf_filename": filename,
            }

        except PwTimeout as exc:
            return False, _normalizar_erro("playwright_timeout", "a página demorou a responder no fluxo JSF", str(exc)[:200])
        except Exception as exc:
            return False, _normalizar_erro("playwright_error", str(type(exc).__name__), str(exc)[:200])
        finally:
            browser.close()


def preparar_modo_assistido(
    ie_cnpj: str,
    codigo_receita: str = "108",
    referencia: str = "",
    tipo_referencia: str = "",
    data_vencimento: str = "",
    valor: float = 10.00,
    data_pagamento: str = "",
    detalhe_receita: str = "",
    contribuinte_modo: str = "inscrito",
    nome_razao_social: str = "",
    cep: str = "",
    logradouro: str = "",
    numero: str = "",
    complemento: str = "",
    bairro: str = "",
) -> ResultadoEmissao:
    if not data_vencimento:
        data_vencimento = datetime.now().strftime("%d/%m/%Y")

    try:
        _validar_entradas(ie_cnpj, codigo_receita, referencia, valor, tipo_referencia, data_pagamento=data_pagamento)
    except ValueError as exc:
        return False, str(exc)

    return True, {
        "mensagem": "modo_assistido",
        "uf": "GO",
        "portal_url": ARR_ENTRADA_URL,
        "campos": {
            "ie_cnpj": ie_cnpj,
            "codigo_receita": codigo_receita,
            "referencia": referencia,
            "tipo_referencia": tipo_referencia,
            "data_vencimento": data_vencimento,
            "data_pagamento": data_pagamento or data_vencimento,
            "valor": float(valor),
            "detalhe_receita": detalhe_receita,
            "contribuinte_modo": contribuinte_modo,
            "nome_razao_social": nome_razao_social,
            "cep": cep,
            "logradouro": logradouro,
            "numero": numero,
            "complemento": complemento,
            "bairro": bairro,
        },
        "instrucoes": [
            "Acesse o fluxo público ARR-GO"
        ]
    }


# ---------------------------------------------------------------------------
# Função pública — listar_receitas
# ---------------------------------------------------------------------------
def listar_receitas(session=None, salvar_cache=True) -> dict:
    from datetime import datetime
    import os, json
    
    options = []
    for cod, desc in MAPEAMENTO_RECEITAS_GO.items():
        options.append({
            "codigo": cod,
            "descricao": desc,
            "extra": {}
        })
        
    resultado = {
        "uf": "GO",
        "atualizado_em": datetime.now().isoformat(),
        "origem": "hardcoded_restrito_jsf",
        "grupos": [
            {
                "nome": "ICMS - GOIAS",
                "options": options
            }
        ]
    }
    
    if salvar_cache:
        mappings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mappings")
        os.makedirs(mappings_dir, exist_ok=True)
        with open(os.path.join(mappings_dir, "GO.json"), "w", encoding="utf-8") as f:
            json.dump(resultado, f, indent=2, ensure_ascii=False)
            
    return resultado


# ---------------------------------------------------------------------------
# Função pública — emitir
# ---------------------------------------------------------------------------
def emitir(session=None, dados_emissao: dict = None, path_pdf: str = "") -> ResultadoEmissao:
    """
    Emite DARE de ICMS para Goiás.
    """
    if dados_emissao is None:
        dados_emissao = {}
        
    ie_cnpj = dados_emissao.get("ie") or dados_emissao.get("ie_cnpj") or dados_emissao.get("cnpj", "")
    codigo_receita = str(dados_emissao.get("receita_codigo", "")).strip()
    referencia = dados_emissao.get("referencia", "")
    tipo_referencia = dados_emissao.get("tipo_referencia", "")
    data_vencimento = dados_emissao.get("data_vencimento", "")
    data_pagamento = dados_emissao.get("data_pagamento", "")
    detalhe_receita = dados_emissao.get("historico", "")
    modo_assistido = dados_emissao.get("modo_assistido", False)
    contribuinte_modo = dados_emissao.get("contribuinte_modo", "inscrito")
    nome_razao_social = dados_emissao.get("nome_razao_social", "")
    cep = dados_emissao.get("cep", "")
    logradouro = dados_emissao.get("logradouro", "")
    numero = dados_emissao.get("numero", "")
    complemento = dados_emissao.get("complemento", "")
    bairro = dados_emissao.get("bairro", "")

    valor_cru = dados_emissao.get("valor", 0.0)
    try: 
        valor_float = float(str(valor_cru).replace(",", ".")) if valor_cru else 0.0
    except ValueError: 
        valor_float = 0.0
        
    if not codigo_receita or not tipo_referencia or not referencia or not data_vencimento or valor_float <= 0:
        return False, "etapa: validar_entrada | motivo: obrigatorios nulos | detalhe: receita, tipo_ref, referencia, dt_venc ou valor invalidos"
        
    if not path_pdf:
        path_pdf = "./pdfs_go"
        
    try:
        _validar_entradas(ie_cnpj, codigo_receita, referencia, valor_float, tipo_referencia, data_pagamento=data_pagamento)
    except ValueError as exc:
        return False, str(exc)

    if modo_assistido:
        return preparar_modo_assistido(
            ie_cnpj=ie_cnpj,
            codigo_receita=codigo_receita,
            referencia=referencia,
            tipo_referencia=tipo_referencia,
            data_vencimento=data_vencimento,
            valor=valor_float,
            data_pagamento=data_pagamento,
            detalhe_receita=detalhe_receita,
            contribuinte_modo=contribuinte_modo,
            nome_razao_social=nome_razao_social,
            cep=cep,
            logradouro=logradouro,
            numero=numero,
            complemento=complemento,
            bairro=bairro,
        )

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        logger.info("Iniciando emissão automatizada via navegador (Playwright - ARR GO)")
        sucesso, retorno = _emitir_via_playwright(
            ie_cnpj=ie_cnpj,
            pdf_path=path_pdf,
            codigo_receita=codigo_receita,
            referencia=referencia,
            tipo_referencia=tipo_referencia,
            data_vencimento=data_vencimento,
            valor=valor_float,
            data_pagamento=data_pagamento,
            detalhe_receita=detalhe_receita,
            contribuinte_modo=contribuinte_modo,
            nome_razao_social=nome_razao_social,
            cep=cep,
            logradouro=logradouro,
            numero=numero,
            complemento=complemento,
            bairro=bairro,
        )
        if sucesso:
            return sucesso, retorno
        else:
            # Classificar: se o erro é de VALIDAÇÃO do portal (contribuinte ou data),
            # não tenta fallback — impede captcha/fallback por erros já conhecidos
            if _erro_e_de_validacao(retorno) or "data de pagamento" in str(retorno).lower():
                logger.warning(
                    "Falha no Playwright (etapa: validação do portal | "
                    "motivo: erro de validação | detalhe: %s). "
                    "Retornando sem fallback.", retorno,
                )
                return False, retorno
            # Erro técnico (timeout, seletor ausente, etc.) → permite fallback
            logger.warning(
                "Falha técnica no Playwright (%s). Tentando fallback legado...",
                retorno,
            )
    except ImportError:
        logger.info("Playwright não encontrado. Usando interface API HTTP fallback...")

    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS_NAV)

    try:
        resp = session.get(DARE_PUBLICO_URL, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()

        if _detectar_login_ou_captcha(resp):
            captcha, msg_captcha = checar_captcha_e_retornar(resp, "GO", "acesso_portal")
            if captcha: return False, msg_captcha
            return False, _normalizar_erro(
                "acesso_portal",
                "portal exige autenticação",
                "o portal mudou a paginação e requer login"
            )

        try:
            csrf = _capturar_csrf(resp.text, "pagina_dare")
        except ValueError:
            csrf = ""

        valor_str = f"{valor_float:.2f}".replace(".", ",")
        payload = {
            "ieCnpj": ie_cnpj,
            "codigoReceita": codigo_receita,
            "detalheReceita": detalhe_receita,
            "referencia": referencia,
            "dataVencimento": data_vencimento,
            "dataPagamento": data_pagamento or data_vencimento,
            "valor": valor_str,
        }
        if csrf: payload["_token"] = csrf

        resp = session.post(
            DARE_PUBLICO_URL,
            data=payload,
            headers={**HEADERS_NAV, "Referer": DARE_PUBLICO_URL},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        if _detectar_login_ou_captcha(resp):
            captcha, msg_captcha = checar_captcha_e_retornar(resp, "GO", "emitir_dare")
            if captcha: return False, msg_captcha
            return False, _normalizar_erro("emitir_dare", "portal redirecionou para login após submissão")

        content_type = resp.headers.get("Content-Type", "")

        if "application/pdf" in content_type.lower():
            try: caminho, nome_arquivo = _baixar_pdf(resp, path_pdf)
            except ValueError as exc: return False, str(exc)
        else:
            soup = BeautifulSoup(resp.text, "html.parser")
            erro_el = soup.find(class_=re.compile(r"erro|danger|alert", re.I))
            if erro_el:
                return False, _normalizar_erro("emitir_dare", "erro do portal", erro_el.get_text(strip=True)[:200])

            link = soup.find("a", href=re.compile(r"pdf|download|imprimir", re.I))
            if not link:
                return False, _normalizar_erro("emitir_dare", "link de download não encontrado")

            pdf_url = link["href"]
            if not pdf_url.startswith("http"):
                pdf_url = "https://portal.sefaz.go.gov.br" + pdf_url

            resp = session.get(pdf_url, timeout=TIMEOUT)
            resp.raise_for_status()

            try: caminho, nome_arquivo = _baixar_pdf(resp, path_pdf)
            except ValueError as exc: return False, str(exc)

        try: from .pdf_utils import validar_pdf
        except ImportError: from pdf_utils import validar_pdf
        is_valido, msg_val = validar_pdf(caminho)
        if not is_valido: return False, _normalizar_erro("validar_pdf_final", "arquivo nulo/corrompido", msg_val)

        logger.info("Emissão concluída: %s", caminho)
        return True, {
            "mensagem": "ok",
            "pdf_path": caminho,
            "pdf_filename": nome_arquivo,
        }

    except requests.RequestException as exc:
        return False, _normalizar_erro("requisicao_http", "falha de conexão", str(exc)[:200])
    except Exception as exc:
        return False, _normalizar_erro("erro_inesperado", type(exc).__name__, str(exc)[:200])
    finally:
        session.close()

if __name__ == "__main__":
    print("=" * 60)
    print("  TESTE DIRETO — Emissão de DARE (ICMS) — GO")
    print("=" * 60)
    print("[!] AVISO: GO utiliza processamento direto HTTP (Requests). Sem restrição por Captcha.\n")

    IE_TESTE = "10.123.456-7"
    PASTA_PDF = "./pdfs_go"

    # Este payload obedece estritamente ao CONTRATO_GO (sem fallbacks destrutivos)
    dados_emissao = {
    "ie_cnpj": "10.472.034-4",
    "receita_codigo": "108",
    "tipo_referencia": "complementar",
    "referencia": "01/2026",
    "data_vencimento": "23/03/2026",
    "data_pagamento": "23/03/2026",
    "valor": 500.00
    }
    
    print(f"[>] Iniciando motor GO com CNPJ/IE {dados_emissao['ie_cnpj']}...\n")
    sucesso, resultado = emitir(
        dados_emissao=dados_emissao,
        path_pdf=PASTA_PDF
    )
    
    if sucesso:
        print(f"\n[SUCESSO] Guia Emitida!")
        print(f"  -> PDF Salvo em: {resultado['pdf_path']}")
    else:
        print(f"\n[FALHA] A automação foi interrompida:\n  -> Motivo: {resultado}")
    print("=" * 60)

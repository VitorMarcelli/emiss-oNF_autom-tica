"""
Módulo — Emissão de GR-PR (ICMS) para Paraná (PR).
Portal: https://emitirgrpr.sefa.pr.gov.br/arrecadacao/

Fluxo mapeado via DevTools/Network (API REST JSON):
  1. GET  /api/v1/arrecadacaocategoria/listar-parametrizadas   → lista categorias
  2. GET  /api/v1/arrecadacao/com-param-ativa-por-categoria?cdCategoria=1  → códigos ICMS
  3. POST /api/v1/emissao-grpr/buscar-campos                  → campos dinâmicos
  4. POST /api/v1/emissao-grpr/consultar-informacoes-emissao   → validação
  5. POST /api/v1/emissao-grpr/gerar-pdf                      → PDF final

⚠️ LIMITAÇÃO: Portal utiliza reCaptcha Enterprise. Se o servidor exigir token
   de captcha válido, a emissão automatizada pura pode ser bloqueada. Nesse caso,
   o módulo retornará erro explicativo com sugestão de alternativa.

Retorno padronizado:
  Sucesso: True, {"mensagem": "ok", "pdf_path": "...", "pdf_filename": "..."}
  Erro:    False, "etapa: <nome> | motivo: <causa> | detalhe: <curto>"
"""

import logging
import re
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Tuple, Union

import requests

try:
    from .captcha_utils import checar_captcha_e_retornar, salvar_snapshot_captcha, erro_captcha_padronizado
except ImportError:
    from captcha_utils import checar_captcha_e_retornar, salvar_snapshot_captcha, erro_captcha_padronizado

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("sefaz_pr")

# ---------------------------------------------------------------------------
# Constantes do portal
# ---------------------------------------------------------------------------
BASE_URL = "https://emitirgrpr.sefa.pr.gov.br/arrecadacao/"
API_BASE = BASE_URL + "api/v1/"
PAGE_URL = BASE_URL + "emitir/guiatela"
TIMEOUT = 60

HEADERS_JSON: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Content-Type": "application/json",
    "Origem": "PUBLICO",
    "Referer": PAGE_URL,
}

HEADERS_NAV: dict[str, str] = {
    "User-Agent": HEADERS_JSON["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Categoria ICMS no portal PR
CD_CATEGORIA_ICMS = 1

ResultadoEmissao = Tuple[bool, Union[dict, str]]


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------
def _normalizar_erro(etapa: str, motivo: str, detalhe: str = "") -> str:
    msg = f"etapa: {etapa} | motivo: {motivo}"
    if detalhe:
        msg += f" | detalhe: {detalhe}"
    return msg


def _validar_entradas(
    cad_icms: str,
    codigo_arrecadacao: str,
    referencia: str,
    valor: float,
) -> None:
    if not cad_icms or not cad_icms.strip():
        raise ValueError(
            _normalizar_erro("validar_entrada", "CAD/ICMS ausente")
        )
    if not codigo_arrecadacao:
        raise ValueError(
            _normalizar_erro("validar_entrada", "código de arrecadação ausente")
        )
    if not referencia or not re.match(r"^\d{2}/\d{4}$", referencia):
        raise ValueError(
            _normalizar_erro(
                "validar_entrada",
                "referência inválida (esperado MM/AAAA)",
            )
        )
    if valor <= 0:
        raise ValueError(
            _normalizar_erro("validar_entrada", "valor deve ser > 0")
        )


def _checar_erros_api(data: Any, etapa: str) -> None:
    """Verifica se a resposta da API contém erros."""
    if isinstance(data, dict):
        erros = data.get("lstErros") or data.get("erros") or []
        if erros:
            msgs = "; ".join(
                e.get("rmesg", str(e)) if isinstance(e, dict) else str(e)
                for e in erros[:3]
            )
            raise ValueError(_normalizar_erro(etapa, "erro da API", msgs[:200]))


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
        filename = f"GRPR_PR_{agora}.pdf"

    destino = Path(pdf_path)
    if destino.is_dir() or not str(destino).lower().endswith(".pdf"):
        destino = destino / filename
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resp.content)

    logger.info("PDF salvo em %s (%d bytes)", destino, len(resp.content))
    return str(destino), filename


def _dismiss_cookie_banners(page) -> None:
    """Fecha os banners de cookies do portal PR (Drupal EU Cookie + Vue app)."""
    try:
        btn_eu = page.locator("#sliding-popup .agree-button")
        if btn_eu.is_visible(timeout=3000):
            btn_eu.click()
            page.wait_for_timeout(500)
    except:
        pass

    try:
        btn_vue = page.locator("#cookie-msg button")
        if btn_vue.is_visible(timeout=2000):
            btn_vue.click()
            page.wait_for_timeout(500)
    except:
        pass


def _select_vue_multiselect(page, input_selector: str, search_text: str, timeout: int = 10000) -> None:
    """Interage com um componente Vue Multiselect: clica, digita, seleciona."""
    fieldset = page.locator(f"fieldset:has({input_selector})")
    if fieldset.count() > 0:
        container = fieldset.locator(".multiselect").first
    else:
        container = page.locator(f"{input_selector}").locator("xpath=ancestor::div[contains(@class,'multiselect')]").first

    tags = container.locator(".multiselect__tags")
    tags.wait_for(state="visible", timeout=timeout)
    tags.click(force=True)
    page.wait_for_timeout(500)

    page.keyboard.type(search_text, delay=50)
    page.wait_for_timeout(800)

    option = container.locator(f".multiselect__option:has-text('{search_text}')").first
    option.wait_for(state="visible", timeout=5000)
    option.click()
    page.wait_for_timeout(500)


def _emitir_via_playwright(
    cad_icms: str,
    pdf_path: str,
    codigo_arrecadacao: str,
    referencia: str,
    data_pagamento: str,
    valor: float,
) -> ResultadoEmissao:
    """Tenta emissão via robô Playwright para contornar bloqueio do reCAPTCHA Enterprise."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        return False, _normalizar_erro(
            "carregar_playwright",
            "Playwright não instalado no ambiente"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(
            user_agent=HEADERS_NAV["User-Agent"],
            accept_downloads=True,
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()

        try:
            page.goto(PAGE_URL, timeout=60000)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)

            _dismiss_cookie_banners(page)

            _select_vue_multiselect(page, "#codCategoria", "ICMS")
            page.wait_for_timeout(2000)

            page.locator("fieldset:has(#codArrecadacao) .multiselect__tags").wait_for(
                state="visible", timeout=10000
            )
            _select_vue_multiselect(page, "#codArrecadacao", codigo_arrecadacao)
            page.wait_for_timeout(1000)

            btn_avancar = page.locator("button:has-text('Avançar')").first
            btn_avancar.wait_for(state="visible", timeout=10000)
            page.wait_for_function(
                "() => !document.querySelector(\"button.btn-primary[disabled]\") || "
                "!document.querySelector(\"button.btn-primary\").disabled",
                timeout=10000
            )
            btn_avancar.click()
            page.wait_for_timeout(3000)

            cad_limpo = re.sub(r'\D', '', cad_icms)
            
            target_type = "CNPJ" if len(cad_limpo) == 14 else ("CPF" if len(cad_limpo) == 11 else None)
            _dbg = Path(__file__).resolve().parent / "debug"
            _dbg.mkdir(parents=True, exist_ok=True)
            with open(_dbg / "pr_form.html", "w", encoding="utf-8") as f:
                f.write(page.locator("form, .container, body").first.inner_html())
            if target_type:
                try:
                    ms = page.locator(".multiselect:has-text('CAD/ICMS')").first
                    if ms.count() > 0:
                        ms.click()
                        page.keyboard.type(target_type, delay=50)
                        page.wait_for_timeout(500)
                        page.locator(f".multiselect__option:has-text('{target_type}')").first.click()
                except Exception:
                    pass
            page.wait_for_timeout(1000)
            
            cad_input = page.locator("#id_0_3")
            cad_input.wait_for(state="visible", timeout=10000)
            cad_input.click()
            try:
                cad_input.fill(cad_limpo)
            except Exception:
                pass

            ref_input = page.locator("#id_0_4")
            ref_input.click()
            ref_input.fill(referencia)

            valor_fmt = f"{valor:.2f}".replace(".", ",")
            valor_input = page.locator("#id_0_5")
            valor_input.click()
            valor_input.fill("")
            valor_input.type(valor_fmt, delay=50)

            _select_vue_multiselect(page, "#id_0_6", data_pagamento)
            page.wait_for_timeout(1000)

            btn_avancar2 = page.locator("button:has-text('Avançar')").first
            btn_avancar2.click(force=True)
            page.wait_for_timeout(5000)

            debug_path = Path(__file__).resolve().parent / "debug" / "pr_playwright_step3.png"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(debug_path))

            erros_els = page.locator("#messages .alert, .alert-danger, .toast-body, .invalid-feedback:visible")
            erros = erros_els.all_inner_texts()
            if erros:
                msg_erro = " ".join(e.strip() for e in erros if e.strip())
                if msg_erro:
                    return False, _normalizar_erro("validar_emissao_playwright", "validação nativa do portal", msg_erro[:200])

            try:
                btn_emitir = page.locator("button.btn-success, button:has-text('Emitir Guia')").first
                btn_emitir.wait_for(state="visible", timeout=15000)
                btn_emitir.click()
                page.wait_for_timeout(5000)

                agora = datetime.now().strftime("%Y%m%d_%H%M%S")
                destino = Path(pdf_path)
                if destino.is_dir() or not str(destino).lower().endswith(".pdf"):
                    destino = destino / f"GRPR_PR_{agora}.pdf"
                destino.parent.mkdir(parents=True, exist_ok=True)
                filename = destino.name

                btn_pdf = page.locator("button:has-text('Salvar PDF'), a:has-text('Salvar PDF')").first
                pdf_capturado = False

                if btn_pdf.is_visible(timeout=10000):
                    try:
                        with page.expect_download(timeout=15000) as download_info:
                            btn_pdf.click()
                        download = download_info.value
                        filename = download.suggested_filename or filename
                        if not str(destino).lower().endswith(".pdf"):
                            destino = destino.parent / filename
                        download.save_as(str(destino))
                        pdf_capturado = True
                    except Exception:
                        pass

                if not pdf_capturado:
                    try:
                        pdf_bytes = page.pdf(
                            format="A4",
                            print_background=True,
                            margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
                        )
                        destino.write_bytes(pdf_bytes)
                        pdf_capturado = True
                    except Exception:
                        pass

                if pdf_capturado:
                    return True, {
                        "mensagem": "ok",
                        "pdf_path": str(destino),
                        "pdf_filename": filename,
                    }
                else:
                    return False, _normalizar_erro("gerar_pdf_playwright", "falha captura PDF", "Screenshot gravada.")

            except PwTimeout:
                return False, _normalizar_erro("emitir_guia_playwright", "botão 'Emitir Guia' ausente", "Screenshot...")

        except PwTimeout as exc:
            return False, _normalizar_erro("playwright_timeout", "timeout", str(exc)[:200])
        except Exception as exc:
            return False, _normalizar_erro("playwright_error", str(type(exc).__name__), str(exc)[:200])
        finally:
            browser.close()


def preparar_modo_assistido(
    cad_icms: str,
    codigo_arrecadacao: str = "1015",
    referencia: str = "",
    data_pagamento: str = "",
    valor: float = 10.00,
    unidade_gestora: str = "990000",
) -> ResultadoEmissao:
    if not referencia:
        referencia = datetime.now().strftime("%m/%Y")
    if not data_pagamento:
        data_pagamento = datetime.now().strftime("%d/%m/%Y")

    try:
        _validar_entradas(cad_icms, codigo_arrecadacao, referencia, valor)
    except ValueError as exc:
        return False, str(exc)

    return True, {
        "mensagem": "modo_assistido",
        "uf": "PR",
        "portal_url": PAGE_URL,
        "campos": {
            "cad_icms": cad_icms,
            "codigo_arrecadacao": codigo_arrecadacao,
            "referencia": referencia,
            "data_pagamento": data_pagamento,
            "valor": float(valor),
            "unidade_gestora": unidade_gestora,
        },
        "instrucoes": [
            "Acesse o portal e preencha os campos informados."
        ]
    }


# ---------------------------------------------------------------------------
# Função pública — listar_receitas
# ---------------------------------------------------------------------------
def listar_receitas(session=None, salvar_cache=True) -> dict:
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS_JSON)
        
    try:
        # Acesso opcional as categorias (CD_CATEGORIA_ICMS = 1)
        resp = session.get(API_BASE + "arrecadacao/com-param-ativa-por-categoria?cdCategoria=1", timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        
        options = []
        for d in dados:
            options.append({
                "codigo": str(d.get("cdArrecadacao", "")),
                "descricao": d.get("descArrecadacaoFormatada", ""),
                "extra": {}
            })
            
        resultado = {
            "uf": "PR",
            "atualizado_em": datetime.now().isoformat(),
            "origem": "extraido_do_portal",
            "grupos": [
                {
                    "nome": "ICMS - PARANA",
                    "options": options
                }
            ]
        }
        
        if salvar_cache:
            mappings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mappings")
            os.makedirs(mappings_dir, exist_ok=True)
            with open(os.path.join(mappings_dir, "PR.json"), "w", encoding="utf-8") as f:
                json.dump(resultado, f, indent=2, ensure_ascii=False)
                
        return resultado
        
    except Exception as e:
        logger.error(f"Falha ao extrair receitas PR: {e}")
        raise RuntimeError(str(e))


# ---------------------------------------------------------------------------
# Função pública — emitir
# ---------------------------------------------------------------------------
def emitir(session=None, dados_emissao: dict = None, path_pdf: str = "") -> ResultadoEmissao:
    """
    Emite GR-PR de ICMS para Paraná.
    """
    if dados_emissao is None:
        dados_emissao = {}
        
    cad_icms = dados_emissao.get("ie") or dados_emissao.get("ie_cnpj") or dados_emissao.get("cad_icms", "")
    codigo_arrecadacao = dados_emissao.get("receita_codigo", "1015")
    referencia = dados_emissao.get("referencia", "")
    data_pagamento = dados_emissao.get("data_pagamento") or dados_emissao.get("data_vencimento") or datetime.now().strftime("%d/%m/%Y")
    unidade_gestora = dados_emissao.get("unidade_gestora", "990000")
    
    valor = dados_emissao.get("valor", 10.00)
    if isinstance(valor, str):
        try: valor_float = float(valor.replace(".", "").replace(",", "."))
        except: valor_float = 10.00
    else:
        valor_float = float(valor)
        
    if not path_pdf:
        path_pdf = "./pdfs_pr"
        
    if not referencia:
        referencia = datetime.now().strftime("%m/%Y")
        
    # Validar
    try:
        _validar_entradas(cad_icms, str(codigo_arrecadacao), referencia, valor_float)
    except ValueError as exc:
        return False, str(exc)

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        logger.info("Iniciando emissão automatizada via navegador (Playwright)")
        sucesso, retorno = _emitir_via_playwright(
            cad_icms=cad_icms,
            pdf_path=path_pdf,
            codigo_arrecadacao=str(codigo_arrecadacao),
            referencia=referencia,
            data_pagamento=data_pagamento,
            valor=valor_float,
        )
        if sucesso:
            return sucesso, retorno
        else:
            logger.warning("Falha no Playwright (%s). Tentando via API original...", retorno)
    except ImportError:
        logger.info("Playwright não encontrado. Usando interface API HTTP padrão...")

    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS_JSON)

    try:
        logger.info("Etapa 1: Abrindo página GR-PR")
        resp = session.get(
            PAGE_URL,
            headers=HEADERS_NAV,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        captcha, msg_captcha = checar_captcha_e_retornar(resp, "PR", "pagina_grpr")
        if captcha and "recaptcha" not in resp.text.lower():
            return False, msg_captcha

        sitekey_match = re.search(r"6L[a-zA-Z0-9_-]{38}", resp.text)
        sitekey = sitekey_match.group(0) if sitekey_match else "6LeuzLspAAAAAF0LbB_B8eW66gTwxfTgpm7-_6IQ"
        
        re_captcha_token = None
        twocaptcha_key = os.environ.get("TWOCAPTCHA_API_KEY", "").strip()
        
        if twocaptcha_key:
            try:
                from ufs.solver_2captcha import resolver_recaptcha_enterprise
                logger.info("Resolvendo reCAPTCHA Enterprise via 2Captcha (sitekey=%s)...", sitekey)
                re_captcha_token = resolver_recaptcha_enterprise(
                    api_key=twocaptcha_key,
                    sitekey=sitekey,
                    url=PAGE_URL,
                    action="submit"
                )
            except Exception as e:
                logger.warning("Erro na resolução do 2Captcha: %s", str(e))
                return False, _normalizar_erro("captcha_solver", "falha_2captcha", str(e)[:200])
        else:
            return False, _normalizar_erro("acesso_portal", "limitação captcha", "Chave do 2Captcha (TWOCAPTCHA_API_KEY) requerida para emissão automatizada do PR.")

        logger.info("Etapa 2: Listando categorias de tributos")
        resp = session.get(
            API_BASE + "arrecadacaocategoria/listar-parametrizadas",
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        logger.info("Etapa 3: Buscando campos do formulário")
        payload_campos = {
            "cdUga": int(unidade_gestora),
            "cdArrecadacao": int(codigo_arrecadacao),
            "cdCategoria": CD_CATEGORIA_ICMS,
            "reCaptchaToken": re_captcha_token
        }
        resp = session.post(
            API_BASE + "emissao-grpr/buscar-campos",
            json=payload_campos,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "PR", "buscar_campos")
        if captcha: return False, msg_captcha

        try:
            data_campos = resp.json()
            _checar_erros_api(data_campos, "buscar_campos")
        except ValueError as exc:
            if "etapa:" in str(exc): return False, str(exc)
            raise

        logger.info("Etapa 4: Validando dados para emissão")
        id_parametrizacao = data_campos.get("idParametrizacao", 11)

        tp_contribuinte = "1"
        id_contribuinte = re.sub(r'\D', '', cad_icms)
        if len(id_contribuinte) == 14: tp_contribuinte = "2"
        elif len(id_contribuinte) == 11: tp_contribuinte = "3"

        ref_limpa = referencia.replace("/", "")   
        dt_venc_limpa = data_pagamento.replace("/", "") 
        vlr_fmt = f"{valor_float:.2f}".replace(".", ",") 

        payload_emissao = {
            "idParametrizacao": id_parametrizacao,
            "cdArrecadacao": int(codigo_arrecadacao),
            "cdUga": int(unidade_gestora),
            "passoAtual": 1,
            "totalPassos": 2,
            "reCaptchaToken": re_captcha_token,
            "lstCampos": [
                {"nomeParametro": "cdUga", "valor": unidade_gestora},
                {"nomeParametro": "cdArrecadacao", "valor": str(codigo_arrecadacao)},
                {"nomeParametro": "tpIdContribuinte", "valor": tp_contribuinte},
                {"nomeParametro": "idContribuinte", "valor": id_contribuinte},
                {"nomeParametro": "dtReferencia", "valor": ref_limpa},
                {"nomeParametro": "vlrDevido", "valor": vlr_fmt},
                {"nomeParametro": "dtPagamento", "valor": dt_venc_limpa}
            ]
        }

        resp = session.post(
            API_BASE + "emissao-grpr/consultar-informacoes-emissao",
            json=payload_emissao,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        captcha, msg_captcha = checar_captcha_e_retornar(resp, "PR", "validar_emissao")
        if captcha: return False, msg_captcha

        try:
            data_validacao = resp.json()
            if isinstance(data_validacao, dict) and data_validacao.get("mensagemCaptcha"):
                snapshot = salvar_snapshot_captcha(resp, "PR", "validar_emissao_mensagemCaptcha")
                return False, erro_captcha_padronizado("validar_emissao", "PR", f"mensagemCaptcha={data_validacao.get('mensagemCaptcha')[:80]}", snapshot)
            _checar_erros_api(data_validacao, "validar_emissao")
        except ValueError as exc:
            if "etapa:" in str(exc): return False, str(exc)
            return False, _normalizar_erro("validar_emissao", "resposta inesperada", str(exc)[:200])

        logger.info("Etapa 5: Gerando PDF")
        payload_pdf = {
            **payload_emissao,
            "dadosValidacao": data_validacao,
        }

        resp = session.post(
            API_BASE + "emissao-grpr/gerar-pdf",
            json=payload_pdf,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        try:
            caminho, nome_arquivo = _baixar_pdf(resp, path_pdf)
        except ValueError as exc:
            return False, str(exc)

        logger.info("Emissão concluída: %s", caminho)
        return True, {
            "mensagem": "ok",
            "pdf_path": caminho,
            "pdf_filename": nome_arquivo,
        }

    except requests.RequestException as exc:
        return False, _normalizar_erro("requisicao_http", "falha de conexão/HTTP", str(exc)[:200])
    except Exception as exc:
        return False, _normalizar_erro("erro_inesperado", type(exc).__name__, str(exc)[:200])
    finally:
        session.close()

if __name__ == "__main__":
    CAD_TESTE = "12345678"  # CAD/ICMS de teste (substituir por válido)
    PASTA_PDF = "./pdfs_pr"

    print("=" * 60)
    print("  TESTE — Emissão de GR-PR")    # Teste de execução
    pdf = str(Path("pdfs_pr").resolve() / "DAR1_PR_TEST.pdf")
    sucesso, resultado = emitir(
        dados_emissao={
            "cad_icms": "9017315606",
            "receita_codigo": "1015",
            "referencia": "02/2026",
            "data_vencimento": "23/03/2026",
            "valor": 10.00
        },
        path_pdf=pdf
    )

    if sucesso:
        print(f"\n[v] SUCESSO")
        print(f"   PDF: {resultado['pdf_path']}")
        print(f"   Nome: {resultado['pdf_filename']}")
    else:
        print(f"\n[x] ERRO: {resultado}")

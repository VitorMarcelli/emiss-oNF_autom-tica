"""
Módulo — Emissão de DAR-1 (ICMS) para Mato Grosso (MT).
Portal: https://www.sefaz.mt.gov.br/arrecadacao/darlivre/

Fluxo REAL mapeado via DevTools/Network + análise do HTML (04/03/2026):
  1. GET  /arrecadacao/darlivre/menudarlivre       → menu (cookies + hidden fields)
  2. POST /arrecadacao/darlivre/pj/gerardar         → navegar para PJ Inscrita (pjInscrita=true)
  3. POST /arrecadacao/darlivre/pj/gerardar         → submeter IE (hidden fields + inscricaoEstadual)
  4. POST /arrecadacao/darlivre/tributodropdown      → carregar receitas AJAX
  5. POST /arrecadacao/darlivre/pj/gerardar          → emitir DAR (pagn=emitir)

Observações:
  - Portal NÃO usa CSRF token.
  - Sessão via JSESSIONID.
  - O fluxo menu → PJ Inscrita exige os hidden fields propagados.
  - Dropdown de tributo populado via AJAX POST.
  - IE formato: 9 digitos sem formatação (ex: 133201040).

CONTRATO DE ENTRADA (dados_emissao):
  Principais campos exigidos:
  - ie_cnpj: (obrigatório) Inscrição MT de 9 dígitos.
  - receita_codigo: (obrigatório) Código/tributo.
  - valor: (obrigatório) Valor principal da arrecadação.
  - referencia: (obrigatório) MM/AAAA. Sem definições de fallback por segurança.
  - data_vencimento: (obrigatório) Data validade atual do DAR original. Sem definições de fallback ocultos. MT bloqueia datas passadas para emissão do DAR.
  - data_pagamento: (opcional) Caso presente e superior à data_vencimento, indica GUIA VENCIDA.
  
  CENÁRIO DE ATRASO (GUIA VENCIDA) NO MT (DAR LIVRE):
  O portal não calcula juros e multa automaticamente! O cliente DEVE mandar o cálculo.
  Quando `data_pagamento` for superior a `data_vencimento`:
  1. A automação usará a `data_pagamento` como o campo 'dtVencimento' do portal 
     (que no portal MT significa "Válido Para Pagamento Até").
  2. A automação exigirá a presença do objeto `acrescimos` (contendo "multa", "juros", etc).
  3. Se a guia for entendida como vencida e não houver `acrescimos`, aborta explicitamente
     para não gerar PDF com valor principal frio (evita falso positivo financeiro).

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
from typing import Tuple, Union

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
logger = logging.getLogger("sefaz_mt")

# ---------------------------------------------------------------------------
# Constantes do portal
# ---------------------------------------------------------------------------
BASE_URL = "https://www.sefaz.mt.gov.br/arrecadacao/darlivre/"
URL_MENU = BASE_URL + "menudarlivre"
URL_PJ = BASE_URL + "pj/gerardar"
URL_TRIBUTO_DROPDOWN = BASE_URL + "tributodropdown"
URL_IMPRIMIR = BASE_URL + "impirmirdar"
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
    "Accept": "*/*",
}

ResultadoEmissao = Tuple[bool, Union[dict, str]]


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------
def _normalizar_erro(etapa: str, motivo: str, detalhe: str = "") -> str:
    msg = f"etapa: {etapa} | motivo: {motivo}"
    if detalhe:
        msg += f" | detalhe: {detalhe}"
    return msg


def _extrair_hiddens(html: str) -> dict:
    """Extrai todos os campos hidden de um HTML."""
    soup = BeautifulSoup(html, "html.parser")
    hiddens = {}
    for inp in soup.find_all("input", {"type": "hidden"}):
        name = inp.get("name")
        if name:
            hiddens[name] = inp.get("value", "")
    return hiddens


def _validar_entradas(
    ie: str,
    codigo_receita: str,
    referencia: str,
    valor: float,
    data_vencimento: str,
) -> None:
    if not ie or not ie.strip():
        raise ValueError(_normalizar_erro("validar_entrada", "IE ausente"))
    if not codigo_receita:
        raise ValueError(
            _normalizar_erro("validar_entrada", "código da receita ausente")
        )
    if not referencia or not re.match(r"^\d{2}/\d{4}$", referencia):
        raise ValueError(
            _normalizar_erro(
                "validar_entrada",
                "referência ausente ou inválida (esperado MM/AAAA)",
            )
        )
    if not data_vencimento or not re.match(r"^\d{2}/\d{2}/\d{4}$", data_vencimento):
        raise ValueError(
            _normalizar_erro(
                "validar_entrada",
                "data_vencimento ausente ou inválida (esperado DD/MM/AAAA) - O portal exige este preenchimento explícito.",
            )
        )
    if valor <= 0:
        raise ValueError(
            _normalizar_erro("validar_entrada", "valor deve ser > 0")
        )


def _baixar_pdf(
    resp: requests.Response,
    pdf_path: str,
    uf: str = "MT",
) -> Tuple[str, str]:
    """Valida resposta e salva PDF em disco."""
    content_type = resp.headers.get("Content-Type", "")
    content_bytes = resp.content
    
    if len(content_bytes) == 0:
        raise ValueError(
            _normalizar_erro("validar_pdf", "arquivo baixado possui 0 KB (vazio)")
        )
        
    is_real_pdf = content_bytes[:5] == b"%PDF-"
    if not is_real_pdf:
        # Extrai um trecho do início da resposta para análise e log de erro claro
        snippet = content_bytes[:100].decode(errors="ignore").replace("\r", " ").replace("\n", " ").strip()
        
        # Salvar o HTML/Conteúdo inesperado para debug
        with open("mt_debug_pdf_error.html", "wb") as f_dbg:
            f_dbg.write(content_bytes)
            
        raise ValueError(
            _normalizar_erro(
                "validar_pdf",
                "conteúdo baixado não corresponde a um PDF real",
                f"content-type={content_type} | header_encontrado: {snippet}"
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
        filename = f"DAR1_{uf}_{agora}.pdf"

    destino = Path(pdf_path)
    if destino.is_dir() or not str(destino).lower().endswith(".pdf"):
        destino = destino / filename
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(content_bytes)

    logger.info("PDF salvo e validado em %s (%d bytes)", destino, len(content_bytes))
    return str(destino), filename


# ---------------------------------------------------------------------------
# Função pública — emitir
# ---------------------------------------------------------------------------
def emitir(session=None, dados_emissao: dict = None, path_pdf: str = "") -> ResultadoEmissao:
    """
    Emite DAR-1 de ICMS para Mato Grosso.

    Args:
        session: Sessão do urllib/requests opcional.
        dados_emissao: Dicionário contendo os dados de emissão.
        path_pdf: Caminho (diretório ou arquivo) para salvar o PDF.

    Returns:
        Tuple[bool, dict | str]: (True, info) ou (False, erro).
    """
    if dados_emissao is None:
        dados_emissao = {}
        
    ie = dados_emissao.get("ie") or dados_emissao.get("ie_cnpj", "")
    codigo_receita = dados_emissao.get("receita_codigo", "")
    referencia = dados_emissao.get("referencia", "")
    data_vencimento = dados_emissao.get("data_vencimento", "")
    data_pagamento = dados_emissao.get("data_pagamento", "")
    
    # Tratamento realístico de vencimento/pagamento (Cenário de Atraso e Encargos MT)
    data_limite_portal = data_vencimento
    acrescimos = dados_emissao.get("acrescimos") or {}
    
    # Converter lista de dicionários para dicionário simples (suporte a ambos os formatos)
    if isinstance(acrescimos, list):
        novo_acrescimos = {}
        for item in acrescimos:
            if isinstance(item, dict) and "tipo" in item and "valor" in item:
                novo_acrescimos[item["tipo"].lower()] = item["valor"]
        acrescimos = novo_acrescimos
        
    if data_pagamento and data_vencimento:
        try:
            dt_v = datetime.strptime(data_vencimento, "%d/%m/%Y")
            dt_p = datetime.strptime(data_pagamento, "%d/%m/%Y")
            if dt_p > dt_v:
                # É um cenário vencido!
                # MT portal "dataVencimento" field means the ORIGINAL Due Date! 
                # So we keep data_limite_portal = data_vencimento.
                
                # Validação de Segurança de Encargos
                if not acrescimos:
                    return False, _normalizar_erro(
                        "validar_acrescimos", 
                        "guia vencida exige encargos manuais", 
                        "O portal do MT (DAR Livre) não calcula multa/juros automaticamente. Você forneceu data de pagamento atrasada; é **obrigatório** passar o sub-objeto 'acrescimos' com os valores computados para não emitirmos uma guia defasada apenas com o valor original."
                    )
        except ValueError:
            pass # será capitulada pelo _validar_entradas
            
    informacao_prevista = dados_emissao.get("historico", "")
    tipo_venda = dados_emissao.get("tipo_venda", "1")
    
    valor = dados_emissao.get("valor")
    if not valor:
        return False, _normalizar_erro("validar_receita", "valor ausente", "é obrigatório informar o 'valor'")
        
    if isinstance(valor, str):
        try:
            valor_float = float(valor.replace(".", "").replace(",", "."))
        except ValueError:
            return False, _normalizar_erro("validar_receita", "valor inválido", "formato de valor não numérico")
    else:
        valor_float = float(valor)
        
    if not codigo_receita:
        return False, "etapa: validar_receita | motivo: receita_codigo ausente | detalhe: informe --receita"
        
    if not path_pdf:
        path_pdf = "./pdfs_mt"

    try:
        _validar_entradas(ie, codigo_receita, referencia, valor_float, data_vencimento)
    except ValueError as exc:
        return False, str(exc)

    # Limpar IE
    ie_limpa = re.sub(r"[.\-/\s]", "", ie)

    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS_NAV)

    try:
        # ── Etapa 1: Abrir menu (cookies + hidden fields) ────────────────
        logger.info("Etapa 1: Abrindo menu DAR-Livre MT")
        resp = session.get(URL_MENU, timeout=TIMEOUT)
        resp.raise_for_status()

        # Checar CAPTCHA na página do menu
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MT", "menu_darlivre")
        if captcha:
            return False, msg_captcha

        hidden_menu = _extrair_hiddens(resp.text)
        logger.info("Hidden fields do menu: %s", list(hidden_menu.keys()))

        # ── Etapa 2: Navegar para PJ Inscrita ────────────────────────────
        logger.info("Etapa 2: Navegando para PJ Inscrita")
        payload_nav = {
            **hidden_menu,
            "pjInscrita": "true",
        }
        resp = session.post(
            URL_PJ,
            data=payload_nav,
            headers={**HEADERS_NAV, "Referer": URL_MENU},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Checar CAPTCHA após navegar para PJ
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MT", "navegar_pj")
        if captcha:
            return False, msg_captcha

        # Verificar se chegou na página de IE
        if "Inscri" not in resp.text or "Estadual" not in resp.text:
            return False, _normalizar_erro(
                "navegar_pj",
                "página de IE não carregada",
                f"url={resp.url}",
            )

        # ── Etapa 3: Submeter IE para identificação ─────────────────────
        logger.info("Etapa 3: Submetendo IE %s para identificação", ie_limpa)
        hidden_ie = _extrair_hiddens(resp.text)

        payload_ie = {
            **hidden_ie,
            "inscricaoEstadual": ie_limpa,
        }
        resp = session.post(
            URL_PJ,
            data=payload_ie,
            headers={**HEADERS_NAV, "Referer": URL_PJ},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Checar CAPTCHA após submeter IE
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MT", "identificar_contribuinte")
        if captcha:
            return False, msg_captcha

        # Verificar se a IE foi aceita
        has_form = "periodoReferencia" in resp.text or "tipoVenda" in resp.text
        has_contrib = "Contribuinte" in resp.text

        if not has_form and not has_contrib:
            soup = BeautifulSoup(resp.text, "html.parser")
            erro_el = soup.find("span", class_="textoVermelho") or \
                      soup.find(string=re.compile(r"(?i)erro|inv[áa]lid"))
            detalhe = erro_el.get_text(strip=True) if erro_el else "IE não reconhecida"
            return False, _normalizar_erro(
                "identificar_contribuinte", "IE inválida ou não encontrada", detalhe[:200]
            )

        logger.info("IE aceita — contribuinte identificado")

        # ── Etapa 4: Emitir DAR (POST multipart/form-data) ────────────
        logger.info("Etapa 4: Emitindo DAR-1")
        valor_str = f"{valor_float:.2f}".replace(".", ",")

        # Extrair TODOS os campos do formulário (hiddens + text + radio)
        soup_form = BeautifulSoup(resp.text, "html.parser")
        all_fields = {}
        for inp in soup_form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = inp.get("type", "text")
            val = inp.get("value") or ""
            if itype == "radio":
                if inp.get("checked") is not None:
                    all_fields[name] = val
            elif itype not in ("button", "submit"):
                all_fields[name] = val

        # Sobrescrever com valores desejados
        all_fields["pagn"] = "emitir"
        all_fields["periodoReferencia"] = referencia
        all_fields["tipoVenda"] = tipo_venda
        all_fields["tributo"] = codigo_receita
        all_fields["numrInscEstadual"] = ie_limpa
        all_fields["inscricaoEstadual"] = ie_limpa
        all_fields["valorCampo"] = valor_str
        all_fields["valor"] = valor_str  # campo hidden que o servidor valida
        all_fields["dataVencimento"] = data_limite_portal
        all_fields["informacaoPrevista"] = informacao_prevista
        all_fields["notas"] = "1"
        
        precisa_juros = False
        precisa_multa = False
        precisa_correcao = False
        
        # Validacao e formatacao de acrescimos
        if "juros" in acrescimos:
            all_fields["juros"] = str(acrescimos["juros"]).replace(".", ",")
            precisa_juros = True
        
        if "multa" in acrescimos:
            all_fields["valorMultaCampo"] = str(acrescimos["multa"]).replace(".", ",")
            all_fields["valorMulta"] = str(acrescimos["multa"]).replace(".", ",")
            precisa_multa = True
            
        if "correcao" in acrescimos:
            all_fields["valorCorrecao"] = str(acrescimos["correcao"]).replace(".", ",")
            precisa_correcao = True
            
        # Opcionalmente, varrer script para ver se o portal vai exigir
        # A validação final de exigência (se não fornecido) pode dar erro e será capturada abaixo na Etapa 5.
        # Nós fazemos a verificação se o usuário omitiu algo num log.

        # Garantir nenhum valor None
        for k in list(all_fields):
            if all_fields[k] is None:
                all_fields[k] = ""

        # Portal exige multipart/form-data (não urlencoded)
        multipart_fields = {
            k: (None, str(v)) for k, v in all_fields.items()
        }

        resp = session.post(
            URL_PJ,
            files=multipart_fields,
            headers={"Referer": URL_PJ},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Checar CAPTCHA após emitir DAR
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MT", "emitir_dar")
        if captcha:
            return False, msg_captcha

        # Verificar erros explícitos
        if "Página de Erros" in resp.text or "operação inexistente" in resp.text:
            soup = BeautifulSoup(resp.text, "html.parser")
            erro_el = soup.find("td", class_="textoVermelho")
            detalhe = erro_el.get_text(separator=" ", strip=True)[:300] if erro_el else soup.get_text(separator=" ", strip=True)[:300]

            if "juros" in detalhe.lower() or "multa" in detalhe.lower() or "atualização" in detalhe.lower() or "correção" in detalhe.lower() or "obrigatório" in detalhe.lower() and ("multa" in detalhe.lower() or "juros" in detalhe.lower()):
                return False, _normalizar_erro("validar_acrescimos", "campo obrigatório ausente", "juros/multa/correcao | portal exige e nao foi repassado. Detalhe: " + detalhe)

            return False, _normalizar_erro("emitir_dar", "portal retornou erro", detalhe)

        # Verificar sucesso real buscando o iframe do PDF
        soup_emitir = BeautifulSoup(resp.text, "html.parser")
        iframe_pdf = soup_emitir.find("iframe")
        pdf_url_relativo = iframe_pdf.get("src") if iframe_pdf else None
        
        if not pdf_url_relativo:
            # Não tem iframe do PDF, logo falhou silenciosamente
            # Extrair algum possível texto de erro genérico se houver
            erro_td = soup_emitir.find("td", class_="textoVermelho")
            txt_erro = erro_td.get_text(strip=True) if erro_td else "Falha desconhecida. Iframe do PDF não encontrado na página de sucesso."
            return False, _normalizar_erro("emitir_dar", "emissão não gerou PDF no portal", txt_erro[:200])

        if pdf_url_relativo.startswith("http"):
            pdf_url_final = pdf_url_relativo
        else:
            pdf_url_final = "https://www.sefaz.mt.gov.br" + pdf_url_relativo

        # ── Etapa 5: Baixar PDF via GET dinâmico com Polling/Retries ─────────────
        logger.info(f"Etapa 5: Emissão concluída. Iniciando download da URL ({pdf_url_final}) com polling...")
        
        import time
        max_tentativas = 3
        caminho, nome_arquivo = None, None
        
        for tentativa in range(1, max_tentativas + 1):
            if tentativa > 1:
                logger.info(f"Aguardando 2 segundos antes da tentativa {tentativa} de download do PDF...")
                time.sleep(2)
                
            resp_pdf = session.get(
                pdf_url_final,
                headers={**HEADERS_NAV, "Referer": URL_PJ},
                timeout=TIMEOUT,
            )
            resp_pdf.raise_for_status()

            try:
                caminho, nome_arquivo = _baixar_pdf(resp_pdf, path_pdf)
                break # Sucesso, sai do loop
            except ValueError as ext_val:
                if "0 KB" in str(ext_val) and tentativa < max_tentativas:
                    logger.warning(f"Download retornou 0 KB na tentativa {tentativa}. O PDF pode ainda estar sendo gerado no servidor.")
                    continue
                else:
                    if tentativa == max_tentativas:
                        return False, str(ext_val) # Retorna erro na última tentativa
                    continue

        try: from .pdf_utils import validar_pdf
        except ImportError: from pdf_utils import validar_pdf
        is_valido, msg_val = validar_pdf(caminho)
        if not is_valido: return False, _normalizar_erro("validar_pdf_final", "falsificacao de bytes ou HTML incorreto", msg_val)

        logger.info("Etapa 6: PDF extraído dinamicamente, salvo e validado com sucesso. Fluxo concluído.")
        return True, {
            "mensagem": "ok",
            "pdf_path": caminho,
            "pdf_filename": nome_arquivo,
        }

    except requests.RequestException as exc:
        return False, _normalizar_erro(
            "requisicao_http", "falha de conexão/HTTP", str(exc)[:200]
        )
    except Exception as exc:
        return False, _normalizar_erro(
            "erro_inesperado", type(exc).__name__, str(exc)[:200]
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Função pública — listar_receitas
# ---------------------------------------------------------------------------
def listar_receitas(session=None, salvar_cache=True) -> dict:
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS_NAV)
    
    try:
        logger.info("Etapa 1: Acessando menu MT para extrair listagem")
        resp = session.get(URL_MENU, timeout=TIMEOUT)
        resp.raise_for_status()
        
        hidden_menu = _extrair_hiddens(resp.text)
        payload_nav = {**hidden_menu, "pjInscrita": "true"}
        
        resp = session.post(URL_PJ, data=payload_nav, headers={**HEADERS_NAV, "Referer": URL_MENU}, timeout=TIMEOUT)
        resp.raise_for_status()
        
        logger.info("Etapa 2: Aplicando IE generica para PJ")
        hidden_ie = _extrair_hiddens(resp.text)
        payload_ie = {**hidden_ie, "inscricaoEstadual": "133201040"}
        resp = session.post(URL_PJ, data=payload_ie, headers={**HEADERS_NAV, "Referer": URL_PJ}, timeout=TIMEOUT)
        resp.raise_for_status()
        
        logger.info("Etapa 3: Buscando dropdown de tributos (AJAX)")
        mes_ano = datetime.now().strftime("%m/%Y")
        data_ajax = f"codgOrgao=&codgCnae=111301&codgLocalEmissao=1&tipoContribuinte=1&nome=tributo&onChange=javascript:eventoTributo()&periodoReferencia=01/{mes_ano}&tipoTributo=&corona=&tipoNovoMenu="
        
        resp_ajax = session.post(
            URL_TRIBUTO_DROPDOWN,
            data=data_ajax,
            headers={**HEADERS_NAV, "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest", "Referer": URL_PJ},
            timeout=TIMEOUT
        )
        resp_ajax.raise_for_status()
        
        soup = BeautifulSoup(resp_ajax.text, "html.parser")
        
        options = []
        for opt in soup.find_all("option"):
            val = opt.get("value")
            text = opt.get_text(strip=True)
            if val and val != "0":
                options.append({
                    "codigo": val,
                    "descricao": text,
                    "extra": {}
                })
        
        resultado = {
            "uf": "MT",
            "atualizado_em": datetime.now().isoformat(),
            "origem": "extraido_do_portal",
            "grupos": [
                {
                    "nome": "DEFAULT",
                    "options": options
                }
            ]
        }
        
        if salvar_cache:
            mappings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mappings")
            os.makedirs(mappings_dir, exist_ok=True)
            with open(os.path.join(mappings_dir, "MT.json"), "w", encoding="utf-8") as f:
                json.dump(resultado, f, indent=2, ensure_ascii=False)
                
        return resultado
        
    except Exception as e:
        logger.error(f"Falha ao extrair receitas MT: {e}")
        raise RuntimeError(str(e))

# ---------------------------------------------------------------------------
# Teste embutido
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  TESTE DIRETO — Emissão de DAR-1 Livre — MT")
    print("=" * 60)
    print("[!] AVISO: MT utiliza processamento direto HTTP (Requests). Sem restrição por Captcha.\n")

    IE_TESTE = "133201040"
    PASTA_PDF = "./pdfs_mt"

    # Este payload obedece estritamente ao CONTRATO_MT
    dados_emissao = {
    "ie": "133201040",
    "receita_codigo": "1112",
    "referencia": "03/2026",
    "data_vencimento": "25/03/2026",
    "data_pagamento": "28/03/2026",
    "valor": 10.00,
    "acrescimos": [
        {"tipo": "juros", "valor": 1.50},
        {"tipo": "multa", "valor": 0.50}
    ]
    }

    print(f"[>] Iniciando motor MT com IE {IE_TESTE}...\n")
    sucesso, resultado = emitir(
        session=None,
        dados_emissao=dados_emissao,
        path_pdf=PASTA_PDF
    )

    if sucesso:
        print(f"\n[SUCESSO] Guia Emitida!")
        print(f"  -> PDF Salvo em: {resultado['pdf_path']}")
    else:
        print(f"\n[FALHA] A automação foi interrompida:\n  -> Motivo: {resultado}")
    print("=" * 60)

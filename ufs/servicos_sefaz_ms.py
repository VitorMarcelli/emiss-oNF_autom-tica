"""
Módulo de referência — Emissão de DAEMS (ICMS) para Mato Grosso do Sul (MS).
Portal: https://servicos.efazenda.ms.gov.br/sgae/EmissaoDAEMSdeICMS/

Fluxo REAL mapeado via DevTools/Network + análise do JS (04/03/2026):
  1. GET  /sgae/EmissaoDAEMSdeICMS/                   → página inicial (cookies)
  2. POST /sgae/EmissaoDAEMSdeICMS/IrParaViewTributo   → seleciona opção e tributo
  3. POST /sgae/EmissaoDAEMSdeICMS/Consultar           → valida dados (AJAX JSON)
  4. POST /sgae/EmissaoDAEMSdeICMS/Emitir              → emite DAEMS (AJAX JSON)
  5. GET  /sgae/EmissaoDAEMSdeICMS/ImprimirPdfDaems    → download do PDF

Observações:
  - Portal NÃO usa CSRF token (__RequestVerificationToken removido).
  - Formulário é submetido via jQuery $.post (AJAX JSON).
  - A seleção de tributo popula opções via JS (Select2 dropdown).
  - IE no formato XX.XXX.XXX-X (máscara 99.999.999-9).
  - Referência no formato MM/AAAA.

Retorno padronizado:
  Sucesso: True, {"mensagem": "ok", "pdf_path": "...", "pdf_filename": "..."}
  Erro:    False, "etapa: <nome> | motivo: <causa> | detalhe: <curto>"
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Tuple, Union

import requests

try:
    from .captcha_utils import checar_captcha_e_retornar
except ImportError:
    from captcha_utils import checar_captcha_e_retornar

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("sefaz_ms")

# ---------------------------------------------------------------------------
# Constantes do portal
# ---------------------------------------------------------------------------
BASE_URL = "https://servicos.efazenda.ms.gov.br/sgae/EmissaoDAEMSdeICMS/"
TIMEOUT = 60  # segundos

HEADERS_NAV = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

HEADERS_AJAX = {
    **HEADERS_NAV,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# Mapa de tipo de identificação -> valor do select
TIPO_IDENTIFICACAO = {
    "IE": "3",
    "Inscrição Estadual": "3",
    "CPF": "1",
    "CNPJ": "2",
}

# Resultado padrão
ResultadoEmissao = Tuple[bool, Union[dict, str]]


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------
def _normalizar_erro(etapa: str, motivo: str, detalhe: str = "") -> str:
    """Formata mensagem de erro padronizada."""
    msg = f"etapa: {etapa} | motivo: {motivo}"
    if detalhe:
        msg += f" | detalhe: {detalhe}"
    return msg


def _validar_entradas(
    ie: str,
    codigo_tributo: str,
    referencia: str,
    valor: float,
) -> None:
    """Valida entradas mínimas antes de iniciar o fluxo."""
    if not ie or not ie.strip():
        raise ValueError(
            _normalizar_erro("validar_entrada", "IE ausente")
        )
    if not codigo_tributo:
        raise ValueError(
            _normalizar_erro("validar_entrada", "código do tributo ausente")
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


def _formatar_ie_ms(ie: str) -> str:
    """Aplica máscara XX.XXX.XXX-X na IE de MS."""
    ie_limpa = re.sub(r"[.\-/\s]", "", ie)
    if len(ie_limpa) == 9 and ie_limpa.isdigit():
        return f"{ie_limpa[:2]}.{ie_limpa[2:5]}.{ie_limpa[5:8]}-{ie_limpa[8]}"
    return ie  # retorna como está se não conseguir formatar


def _baixar_pdf(
    session: requests.Session,
    pdf_path: str,
) -> Tuple[str, str]:
    """Faz download do PDF via ImprimirPdfDaems e salva em disco."""
    url_pdf = BASE_URL + "ImprimirPdfDaems"
    logger.info("Baixando PDF de %s", url_pdf)
    resp = session.get(
        url_pdf,
        headers=HEADERS_NAV,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    is_pdf = "application/pdf" in content_type.lower()
    if not is_pdf and resp.content[:5] == b"%PDF-":
        is_pdf = True

    if not is_pdf:
        raise ValueError(
            _normalizar_erro(
                "baixar_pdf",
                "resposta não é PDF",
                f"content-type={content_type}",
            )
        )

    # Extrair filename do Content-Disposition ou gerar fallback
    filename = None
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        match = re.search(r'filename="?([^";\r\n]+)"?', cd)
        if match:
            filename = match.group(1).strip()

    if not filename:
        agora = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"DAEMS_MS_{agora}.pdf"

    destino = Path(pdf_path)
    if destino.is_dir() or not str(destino).lower().endswith(".pdf"):
        destino = destino / filename
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resp.content)

    logger.info("PDF salvo em %s (%d bytes)", destino, len(resp.content))
    return str(destino), filename


# ---------------------------------------------------------------------------
# Função pública — emitir
# ---------------------------------------------------------------------------
def emitir(session=None, dados_emissao: dict = None, path_pdf: str = "", **kwargs) -> ResultadoEmissao:
    """
    Emite DAEMS de ICMS para Mato Grosso do Sul.

    Args:
        session: Sessão requests (opcional, criada internamente se ausente).
        dados_emissao: Dicionário com dados de emissão (interface padrão).
        path_pdf: Caminho (diretório ou arquivo) para salvar o PDF.

    Returns:
        Tuple[bool, dict | str]: (True, info_dict) ou (False, erro_string).
    """
    if dados_emissao is None:
        dados_emissao = {}

    ie = dados_emissao.get("ie") or dados_emissao.get("ie_cnpj") or dados_emissao.get("cad_icms", "")
    codigo_tributo = dados_emissao.get("receita_codigo", "")
    referencia = dados_emissao.get("referencia", "")
    data_vencimento_payload = dados_emissao.get("data_vencimento", "")
    data_pagamento = dados_emissao.get("data_pagamento") or data_vencimento_payload
    historico = dados_emissao.get("historico", "ICMS NORMAL")
    opcao = dados_emissao.get("opcao", "Nao")
    tipo_identificacao = dados_emissao.get("tipo_identificacao", "IE")
    razao_social = dados_emissao.get("razao_social", "")
    vencimento_prop = "1" # Operacional padrao MS DAEMS para Vencimento Mensal

    valor_cru = dados_emissao.get("valor", 0.0)
    if isinstance(valor_cru, str):
        try:
            valor = float(valor_cru.replace(".", "").replace(",", "."))
        except Exception:
            valor = 0.0
    else:
        try:
            valor = float(valor_cru)
        except (ValueError, TypeError):
            valor = 0.0

    pdf_path = path_pdf or "./pdfs_ms"

    # Strict Validation Sem Defaults Ocultos
    if not data_pagamento:
        return False, _normalizar_erro("validar_entrada", "data_vencimento ou data_pagamento ausentes no payload", "O MS exige uma representacao de data de vencimento/pagamento. Nao recriamos dias padroes do sistema.")
        
    try:
        _validar_entradas(ie, codigo_tributo, referencia, valor)
    except ValueError as exc:
        return False, str(exc)

    # Formatar IE com máscara MS
    ie_formatada = _formatar_ie_ms(ie)

    # Resolver tipo de identificação
    tipo_doc = TIPO_IDENTIFICACAO.get(tipo_identificacao, "3")

    # Formatar valor no padrão brasileiro
    valor_str = f"{valor:.2f}".replace(".", ",")

    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS_NAV)

    try:
        # ── Etapa 1: Carregar página inicial (cookies) ────────────────
        logger.info("Etapa 1: Abrindo página inicial do DAEMS-MS")
        resp = session.get(BASE_URL, timeout=TIMEOUT)
        resp.raise_for_status()

        # Checar redirect para login
        if "login" in resp.url.lower():
            return False, _normalizar_erro(
                "pagina_inicial",
                "portal redirecionou para login",
            )

        # Checar CAPTCHA na página inicial
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MS", "pagina_inicial")
        if captcha:
            return False, msg_captcha

        # ── Etapa 2: Selecionar tributo ──────────────────────────────
        logger.info("Etapa 2: Selecionando tributo %s (opção=%s)", codigo_tributo, opcao)
        url_tributo = BASE_URL + "IrParaViewTributo"
        resp = session.post(
            url_tributo,
            data={"Opcao": opcao, "Codigo": codigo_tributo},
            headers=HEADERS_NAV,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Checar CAPTCHA após seleção de tributo
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MS", "selecionar_tributo")
        if captcha:
            return False, msg_captcha

        if "login" in resp.url.lower():
            return False, _normalizar_erro(
                "selecionar_tributo",
                "portal redirecionou para login",
            )

        # ── Etapa 3: Consultar (validar dados) ───────────────────────
        logger.info("Etapa 3: Consultando/validando dados via AJAX")
        url_consultar = BASE_URL + "Consultar"

        payload_consultar = {
            "tributo": codigo_tributo,
            "tipoDocumento": tipo_doc,
            "documento": ie_formatada,
            "razaoSocial": razao_social,
            "vencimento": vencimento_prop,
            "referencia": referencia,
            "dataPagamento": data_pagamento,
            "valor": valor_str,
            "Historico1": historico,
            "tipoVencimento": vencimento_prop,
        }

        resp = session.post(
            url_consultar,
            data=payload_consultar,
            headers=HEADERS_AJAX,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Checar CAPTCHA após consulta
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MS", "consultar")
        if captcha:
            return False, msg_captcha

        try:
            data = resp.json()
        except ValueError:
            return False, _normalizar_erro(
                "consultar",
                "resposta não é JSON",
                f"content-type={resp.headers.get('Content-Type', '')}",
            )

        # Checar erros de validação
        if "message" in data and data["message"]:
            return False, _normalizar_erro(
                "consultar",
                "portal retornou mensagem de erro",
                data["message"][:200],
            )

        if "erros" in data and data["erros"]:
            erros = data["erros"]
            partes_erro = []
            for chave, valor_erro in erros.items():
                if valor_erro is not None:
                    partes_erro.append(f"{chave}={valor_erro}")
            if partes_erro:
                return False, _normalizar_erro(
                    "consultar",
                    "validação do portal",
                    "; ".join(partes_erro)[:200],
                )

        logger.info("Consulta validada com sucesso")

        # ── Etapa 4: Emitir DAEMS ────────────────────────────────────
        logger.info("Etapa 4: Emitindo DAEMS via AJAX")
        url_emitir = BASE_URL + "Emitir"

        payload_emitir = {
            "tributo": codigo_tributo,
            "documento": ie_formatada,
            "razaoSocial": razao_social,
            "vencimento": vencimento_prop,
            "referencia": referencia,
            "dataPagamento": data_pagamento,
            "valor": valor_str,
            "Historico1": historico,
            "operacao": "1",
            "tipoVencimento": vencimento_prop,
            "EmailEnvioDaems": "",
        }

        resp = session.post(
            url_emitir,
            data=payload_emitir,
            headers=HEADERS_AJAX,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Checar CAPTCHA após emissão
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "MS", "emitir_daems")
        if captcha:
            return False, msg_captcha

        try:
            data = resp.json()
        except ValueError:
            return False, _normalizar_erro(
                "emitir_daems",
                "resposta não é JSON",
                f"content-type={resp.headers.get('Content-Type', '')}",
            )

        mensagem = data.get("mensagem", "")
        if mensagem not in ("OK", "OKICMS"):
            return False, _normalizar_erro(
                "emitir_daems",
                "portal retornou erro na emissão",
                mensagem[:200],
            )

        logger.info("Emissão confirmada: %s", mensagem)

        # ── Etapa 5: Baixar PDF ──────────────────────────────────────
        logger.info("Etapa 5: Solicitando download do PDF")
        try:
            caminho, nome_arquivo = _baixar_pdf(session, pdf_path)
        except ValueError as exc:
            return False, str(exc)

        # Validação Final Transversal de PDF (Correção 17)
        try: from .pdf_utils import validar_pdf
        except ImportError: from pdf_utils import validar_pdf
        
        is_valido, msg_val = validar_pdf(caminho)
        if not is_valido:
            return False, _normalizar_erro("validar_pdf_final", "PDF falso ou corrompido", msg_val)

        logger.info("Emissão concluída com sucesso: %s", caminho)
        return True, {
            "mensagem": "ok",
            "pdf_path": caminho,
            "pdf_filename": nome_arquivo,
        }

    except requests.RequestException as exc:
        return False, _normalizar_erro(
            "requisicao_http",
            "falha de conexão/HTTP",
            str(exc)[:200],
        )
    except Exception as exc:
        return False, _normalizar_erro(
            "erro_inesperado",
            type(exc).__name__,
            str(exc)[:200],
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Teste embutido
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  TESTE DIRETO — Emissão de DAEMS (ICMS) — MS")
    print("=" * 60)
    print("[!] AVISO: MS utiliza rotas HTTP diretas. Não exige Captcha.\n")

    IE_TESTE = "28.348.179-0"
    PASTA_PDF = "./pdfs_ms"

    # Este payload obedece estritamente ao CONTRATO_MS
    dados_emissao = {
        "ie": IE_TESTE,
        "receita_codigo": "310",
        "referencia": "12/2026",
        "data_vencimento": "15/01/2027",
        "valor": 10.00,
        "historico": "TESTE ICMS NORMAL"
    }

    print(f"[>] Iniciando motor MS com IE {IE_TESTE}...\n")
    sucesso, resultado = emitir(
        dados_emissao=dados_emissao,
        path_pdf=PASTA_PDF,
    )

    if sucesso:
        print(f"\n[SUCESSO] Guia Emitida!")
        print(f"  -> PDF Salvo em: {resultado['pdf_path']}")
    else:
        print(f"\n[FALHA] A automação foi interrompida:\n  -> Motivo: {resultado}")
    print("=" * 60)

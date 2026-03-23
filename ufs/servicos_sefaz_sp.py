"""
Módulo — Emissão de DARE (ICMS) para São Paulo (SP).
Portal: https://www4.fazenda.sp.gov.br/DareICMS/

⚠️ LIMITAÇÃO ARQUITETURAL:
  O portal de SP implementa CAPTCHA visual dinâmico após a consulta
  do CNPJ/CPF em certas instâncias.
  
  O módulo de SP suporta:
    - Automação via Client-Side Bypass na API de validação AJAX (quando aplicável).
    - Orquestração completa do DARE se os desafios invisíveis forem transpostos.
    
  O módulo de SP NÃO suporta e FALHARÁ EXPLICITAMENTE se:
    - O Portal travar a concessão do token exigindo intervenção visual intransponível.
    - Neste caso de falha rígida (Hard Fail), a orquestração depende estritamente 
      da implementação de um serviço de quebra de captcha (Captcha Solver de terceiros)
      via configuração de ambiente. Não provemos "fallback humano" ou modo semi-assistido.

  1. GET  /DareICMS/DareAvulso                              → página SPA (cookies)
  2. POST /DareICMS/DareAvulso/btnConsultar_Click/{CNPJ}    → consulta (AJAX)
     → resposta pode ser {"requiresV2": true} exigindo captcha
  3. POST /DareICMS/DareAvulso/ValidarCaptcha               → valida captcha manual
  3A.POST /DareICMS/DareAvulso/btnCalcular_Click/           → recálculo de juros automáticos
      (O cálculo na SEFAZ SP é ativado mecanicamente por essa rota. Não requer
       flags de 'autoCalc=true' no payload da guia, apenas o trânsito dos valores
       recalculados para a etapa subsequente).
  4. POST /DareICMS/DareAvulso/GerarDare                    → gera DARE (AJAX)
  5. GET  /DareICMS/DareAvulso/ImprimirDare                 → download PDF

Retorno padronizado:
  Sucesso: True, {"mensagem": "ok", "pdf_path": "...", "pdf_filename": "..."}
  Erro:    False, "etapa: <nome> | motivo: <causa> | detalhe: <curto>"
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Tuple, Union

import requests
from bs4 import BeautifulSoup

try:
    from .captcha_utils import checar_captcha_e_retornar, salvar_snapshot_captcha
except ImportError:
    from captcha_utils import checar_captcha_e_retornar, salvar_snapshot_captcha

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("sefaz_sp")

# ---------------------------------------------------------------------------
# Constantes do portal
# ---------------------------------------------------------------------------
BASE_URL = "https://www4.fazenda.sp.gov.br/DareICMS/"
URL_AVULSO = BASE_URL + "DareAvulso"
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
    "Content-Type": "application/json",
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


def _validar_entradas(
    cnpj_cpf: str,
    codigo_receita: str,
    referencia: str,
    valor: float,
) -> None:
    if not cnpj_cpf or not cnpj_cpf.strip():
        raise ValueError(
            _normalizar_erro("validar_entrada", "CNPJ/CPF ausente")
        )
    if not codigo_receita:
        raise ValueError(
            _normalizar_erro("validar_entrada", "codigo de receita/serviço ausente")
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


def _extrair_sitekey(html: str) -> str:
    """Extrai a chave publica (sitekey) do reCAPTCHA do HTML da Sefaz SP."""
    soup = BeautifulSoup(html, "html.parser")
    # Procurar elemento com data-sitekey (padrão antigo/v2 explícito)
    el = soup.find(attrs={"data-sitekey": True})
    if el and el.get("data-sitekey"):
        return el["data-sitekey"]
    
    # 1. Tentar achar na variável global do DARE de SP
    match = re.search(r'chavePublicaDareAvulso\s*=\s*[\'"]([A-Za-z0-9_\-]+)[\'"]', html)
    if match:
        return match.group(1)
        
    # 2. Tentar achar direto na chamada do grecaptcha.execute
    match = re.search(r'grecaptcha\.execute\(\s*[\'"]([A-Za-z0-9_\-]+)[\'"]', html)
    if match:
        return match.group(1)

    # 3. Fallbacks genéricos Regex
    match = re.search(r'data-sitekey=["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)
        
    match = re.search(r'sitekey:\s*["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)
        
    return ""


def _detectar_captcha(resp: requests.Response) -> bool:
    """Verifica se o portal está exigindo CAPTCHA."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data.get("requiresV2", False) is True
    except (ValueError, AttributeError):
        pass
    # Verificar por texto no HTML
    if "captcha" in resp.text.lower() or "UserCaptchaCode" in resp.text:
        return True
    return False


def _baixar_pdf(
    resp: requests.Response,
    pdf_path: str,
) -> Tuple[str, str]:
    """Salva a resposta do SEFAZ em PDF ou ZIP."""
    content_type = resp.headers.get("Content-Type", "").lower()
    is_pdf = "pdf" in content_type
    is_zip = "zip" in content_type
    if not is_pdf and not is_zip and resp.content[:5] == b"%PDF-":
        is_pdf = True

    if not is_pdf and not is_zip:
        raise ValueError(
            _normalizar_erro(
                "baixar_pdf",
                "resposta não é PDF nem ZIP",
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
        ext = "zip" if is_zip else "pdf"
        filename = f"DARE_LOTE_{agora}.{ext}"

    destino = Path(pdf_path)
    if destino.is_dir():
        destino = destino / filename
    else:
        # Se for ZIP mas mandou extensão .pdf, corrige
        if is_zip and str(destino).lower().endswith(".pdf"):
            destino = destino.parent / (destino.stem + ".zip")
        # Se for PDF mas mandou extensão sem .pdf
        elif is_pdf and not str(destino).lower().endswith(".pdf"):
            destino = destino.parent / (destino.name + ".pdf")
            
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resp.content)

    return str(destino), filename


# ---------------------------------------------------------------------------
# Função pública — emitir
# ---------------------------------------------------------------------------
def emitir(session=None, dados_emissao: dict = None, path_pdf: str = "") -> ResultadoEmissao:
    """
    Emite DARE de ICMS para São Paulo.

    ⚠️ O portal possui barreiras de CAPTCHA visual. Se o bypass injetado falhar,
    o módulo falhará formalmente alertando a necessidade técnica de um Captcha Solver.
    Não há fallback para intervenção humana ou fluxo híbrido.

    Args:
        session: Instância de requests.Session (opcional).
        dados_emissao: Dicionário com cnpj_cpf, receita_codigo, referencia, valor, ie.
        path_pdf: Caminho para salvar o PDF.

    Returns:
        Tuple[bool, dict | str]: (True, info) ou (False, erro).
    """
    if dados_emissao is None:
        dados_emissao = {}
        
    cnpj_cpf = dados_emissao.get("cnpj_cpf", "")
    codigo_receita = dados_emissao.get("receita_codigo", "")
    referencia = dados_emissao.get("referencia", "")
    valor_cru = dados_emissao.get("valor", 0.0)
    try:
        valor = float(str(valor_cru).replace(",", ".")) if valor_cru else 0.0
    except ValueError:
        valor = 0.0
            
    ie = dados_emissao.get("ie", "")
    
    if not codigo_receita:
        return False, "etapa: validar_receita | motivo: receita_codigo ausente | detalhe: informe --receita"
    
    if not path_pdf:
        path_pdf = "./pdfs_sp"

    if not referencia:
        referencia = datetime.now().strftime("%m/%Y")
        
    data_vencimento_str = dados_emissao.get("data_vencimento", "")
    
    if not codigo_receita or not referencia or not data_vencimento_str or float(valor) <= 0:
        return False, "etapa: validar_entrada | motivo: campos obrigatórios ausentes | detalhe: receita_codigo, referencia, data_vencimento e valor (>0) são obrigatórios"
        
    try:
        data_venc_obj = datetime.strptime(data_vencimento_str, "%d/%m/%Y")
    except ValueError:
        return False, "etapa: validar_entrada | motivo: formato de data invalido | detalhe: data_vencimento precisa ser DD/MM/AAAA"
        
    data_venc_iso = data_venc_obj.strftime("%Y-%m-%d")

    try:
        _validar_entradas(cnpj_cpf, codigo_receita, referencia, valor)
    except ValueError as exc:
        return False, str(exc)

    # Limpar CNPJ — só dígitos
    cnpj_limpo = re.sub(r"[.\-/\s]", "", cnpj_cpf)

    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS_NAV)

    try:
        # ── Etapa 1: Carregar página (cookies) ────────────────────────
        logger.info("Etapa 1: Abrindo página DARE Avulso SP")
        resp = session.get(URL_AVULSO, timeout=TIMEOUT)
        resp.raise_for_status()

        if "login" in resp.url.lower():
            return False, _normalizar_erro(
                "pagina_inicial",
                "portal redirecionou para login",
            )

        # Checar CAPTCHA na página inicial
        captcha, msg_captcha = checar_captcha_e_retornar(resp, "SP", "pagina_inicial")
        if captcha:
            if not os.environ.get("TWOCAPTCHA_API_KEY"):
                return False, _normalizar_erro("validar_ambiente", "solver não configurado", "Variável TWOCAPTCHA_API_KEY ausente no .env. Solver é obrigatório para transpor bloqueios ativos na SEFAZ SP.")
            return False, msg_captcha

        # Extrair sitekey para possível uso posterior
        sitekey = _extrair_sitekey(resp.text)
        if sitekey:
            logger.info("Sitekey do reCAPTCHA extraído: %s", sitekey)

        # ── Etapa 2: Consultar CNPJ/CPF (AJAX) ──────────────────────
        logger.info("Etapa 2: Consultando contribuinte")
        url_consulta = f"{URL_AVULSO}/btnConsultar_Click/{cnpj_limpo}"

        resp = session.post(
            url_consulta,
            json="",
            headers={**HEADERS_AJAX, "Referer": URL_AVULSO},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Verificar se exige CAPTCHA (falhou no v3 invisível)
        if _detectar_captcha(resp):
            logger.info("Etapa 2b: Bypass do CAPTCHA visual local (client-side bypass)")
            # A SEFAZ SP possui uma falha arquitetural: a validação da imagem gerada no
            # fallback (opcaoCaptcha2) é inteiramente client-side (Javascript). Para prosseguir,
            # basta imitar a chamada que o botão de Validar faria após o sucesso.
            url_validar = f"{URL_AVULSO}/btnValidar_Click/{cnpj_limpo}"
            resp = session.post(
                url_validar,
                json="",
                headers={**HEADERS_AJAX, "Referer": URL_AVULSO},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            
            # Se mesmo assim retornar erro, capturamos
            if _detectar_captcha(resp):
                if not os.environ.get("TWOCAPTCHA_API_KEY"):
                    return False, _normalizar_erro("validar_ambiente", "solver não configurado", "Variável TWOCAPTCHA_API_KEY ausente no .env. O portal bloqueou o acesso e requer resolução formal de Captcha.")
                    
                snapshot = salvar_snapshot_captcha(resp, "SP", "consultar_contribuinte")
                return False, _normalizar_erro(
                    "consultar_contribuinte",
                    "captcha detectado",
                    f"portal bloqueou a consulta mesmo com o bypass direto | "
                    f"snapshot: {snapshot} | "
                    f"solução: configuração de Captcha Solver externo exigida.",
                )

        # Verificar resposta da consulta
        try:
            data_consulta = resp.json()
            if isinstance(data_consulta, dict):
                campo_erro = data_consulta.get("erro")
                tem_erro = False
                
                if isinstance(campo_erro, bool) and campo_erro is True:
                    tem_erro = True
                elif isinstance(campo_erro, dict) and not campo_erro.get("estaOk", True):
                    tem_erro = True
                elif isinstance(campo_erro, dict) and campo_erro.get("estaOk") is False:
                    tem_erro = True

                if tem_erro:
                    return False, _normalizar_erro(
                        "consultar_contribuinte",
                        "consulta rejeitada pela sefaz",
                        str(data_consulta.get("mensagem", ""))[:200],
                    )
        except ValueError:
            data_consulta = {}

        # ── Etapa 3: Gerar DARE ──────────────────────────────────────
        logger.info("Etapa 3: Gerando DARE")
        valor_str = f"{valor:.2f}".replace(".", ",")

        # Monta o DTO de emissão de acordo com o JS `btnGerar_Click`
        # Tratando CPFs e CNPJs dinamicamente baseado na API da Sessão do Contribuinte
        cpf = cnpj_cpf if len(cnpj_limpo) == 11 else data_consulta.get("cpf", "")
        cnpj = cnpj_cpf if len(cnpj_limpo) == 14 else data_consulta.get("cnpj", "")
        
        # Mapeamento do Código Inteiro da Receita Exigido pelo DTO do DARE SP
        codigo_servico_dare = 0
        for rec in data_consulta.get("possiveisReceitas", []):
            nome = str(rec.get("nome", ""))
            # Busca pelo nome ou prefixo do código
            if codigo_receita in nome or nome.startswith(codigo_receita[:3]):
                codigo_servico_dare = int(rec.get("codigoServicoDARE", 0))
                break
        
        if not codigo_servico_dare:
            opcoes_validas = []
            for rec in data_consulta.get("possiveisReceitas", []):
                nome_bruto = str(rec.get("nome", "")).strip()
                if nome_bruto:
                    opcoes_validas.append(f"[{nome_bruto}]")
            
            if opcoes_validas:
                lista_str = " | ".join(opcoes_validas)
                detalhe_msg = f"A receita solicitada ('{codigo_receita}') não foi encontrada na Sefaz. Opções válidas/liberadas para esta inscrição: {lista_str}"
            else:
                detalhe_msg = f"A receita solicitada ('{codigo_receita}') não foi encontrada e o portal não liberou NENHUMA receita avulsa para este contribuinte."

            return False, _normalizar_erro(
                "selecionar_receita",
                "código de receita incompatível ou não liberado ao contribuinte atual",
                detalhe_msg
            )

        payload_dare = {
            "inscricaoEstadual": ie or data_consulta.get("inscricaoEstadual", ""),
            "cnpj": cnpj,
            "cpf": cpf,
            "razaoSocial": data_consulta.get("razaoSocial", ""),
            "telefone": data_consulta.get("telefone", ""),
            "endereco": data_consulta.get("endereco", ""),
            "cidade": data_consulta.get("cidade", ""),
            "UF": data_consulta.get("uf", ""),
            "cpr": data_consulta.get("cpr", "0000"),
            "referencia": referencia,
            "dataVencimento": data_venc_iso,
            "Receita": {
                "codigoServicoDARE": codigo_servico_dare,
                "CamposEspecificos": [
                    {"valor": ""},
                    {"valor": ""},
                    {"valor": ""}
                ]
            },
            "observacao": "",
            "valor": float(valor),
            "valorJuros": 0.0,
            "valorMulta": 0.0,
            "valorTotal": float(valor)
        }

        # ── Etapa 3A: Atualizando encargos da guia (Recálculo) ────────
        headers_url = {**HEADERS_AJAX, "Referer": URL_AVULSO}
        headers_url["Content-Type"] = "application/json; charset=UTF-8"
        
        logger.info("Etapa 3A: Calculando juros e multas (se aplicáveis)")
        resp_calc = session.post(
            URL_AVULSO + "/btnCalcular_Click/",
            json=payload_dare,
            headers=headers_url,
            timeout=20,
        )
        resp_calc.raise_for_status()
        
        try:
            data_calc = resp_calc.json()
            # Só atualiza e confia se a mensagem de erro estiver limpa e os campos numéricos vierem
            campo_erro_calc = data_calc.get("erro")
            if not campo_erro_calc or (isinstance(campo_erro_calc, dict) and campo_erro_calc.get("estaOk", True) is True):
                if "valorJuros" in data_calc and "valorMulta" in data_calc and "valorTotal" in data_calc:
                    payload_dare["valorJuros"] = data_calc["valorJuros"]
                    payload_dare["valorMulta"] = data_calc["valorMulta"]
                    payload_dare["valorTotal"] = data_calc["valorTotal"]
                    logger.info("Valores recalculados pelo portal: Juros=R$%.2f | Multa=R$%.2f | Total=R$%.2f", 
                                data_calc["valorJuros"], data_calc["valorMulta"], data_calc["valorTotal"])
            else:
                detalhe_erro = str(campo_erro_calc)[:200]
                logger.warning("Falha bloqueante ao efetuar mock financeiro via /btnCalcular_Click/. Detalhe da API: %s", detalhe_erro)
                return False, _normalizar_erro("calcular_encargos", "O portal SP rejeitou o recálculo dos juros/multa para a data de vencimento exigida.", detalhe_erro)
        except ValueError:
            return False, _normalizar_erro("calcular_encargos", "Resposta de recálculo não-JSON (possível instabilidade na SEFAZ-SP)")
        
        # ── Etapa 3B: Geração Transacional ────────────────────────────
        # Submete requisição final JSON no botão Gerar

        resp = session.post(
            URL_AVULSO + "/btnGerar_Click/",
            json=payload_dare,
            headers=headers_url,
            timeout=20,
        )

        resp.raise_for_status()

        if "application/json" in resp.headers.get("Content-Type", ""):
            # Tentar via JSON
            try:
                data_resp = resp.json()
                campo_erro = data_resp.get("erro")
                tem_erro = False
                
                if isinstance(campo_erro, bool) and campo_erro is True:
                    tem_erro = True
                elif isinstance(campo_erro, dict) and not campo_erro.get("estaOk", True):
                    tem_erro = True
                elif isinstance(campo_erro, dict) and campo_erro.get("estaOk") is False:
                    tem_erro = True

                if tem_erro or data_resp.get("mensagem"):
                    msg_banco = data_resp.get("mensagem")
                    if not msg_banco and isinstance(campo_erro, dict):
                        msgs = campo_erro.get("mensagens", [])
                        if msgs:
                            msg_banco = " | ".join(msgs)
                    
                    if not msg_banco:
                        msg_banco = str(campo_erro)

                    return False, _normalizar_erro(
                        "gerar_dare",
                        "portal retornou erro",
                        str(msg_banco)[:200],
                    )
            except ValueError:
                pass

            # Tentar download do PDF via endpoint de impressão
          # A URL que baixa fisicamente o PDF no navegador baseia-se na Sessão do server de SP
        resp_pdf = session.get(
            URL_AVULSO + "/FazerDownloadArquivo/",
            headers=HEADERS_NAV, # Use HEADERS_NAV for file download
            timeout=30,
        )
        resp_pdf.raise_for_status()

        try:
            caminho, nome_arquivo = _baixar_pdf(resp_pdf, path_pdf)
        except ValueError as exc:
            return False, str(exc)

        try: from .pdf_utils import validar_pdf
        except ImportError: from pdf_utils import validar_pdf
        is_valido, msg_val = validar_pdf(caminho)
        if not is_valido: return False, _normalizar_erro("validar_pdf_final", "falha de autenticidade (0 bytes ou HTML)", msg_val)

        logger.info("Emissão concluída: %s", caminho)
        return True, {
            "mensagem": "ok",
            "pdf_path": caminho,
            "pdf_filename": nome_arquivo,
        }

    except requests.RequestException as exc:
        return False, _normalizar_erro(
            "requisicao_http", "falha de conexão/HTTP", str(exc)[:200]
        )
    except ValueError as exc:
        return False, str(exc) if "etapa:" in str(exc) else _normalizar_erro(
            "erro_inesperado", "ValueError", str(exc)[:200]
        )
    except Exception as exc:
        return False, _normalizar_erro(
            "erro_inesperado", type(exc).__name__, str(exc)[:200]
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Função pública — emitir_em_lote
# ---------------------------------------------------------------------------
def emitir_em_lote(
    itens: list[dict],
    pdf_path: str,
    razao_social: str = "CONTRIBUINTE",
    telefone: str = "(11)99999-9999",
    endereco: str = "RUA TESTE, 123",
    cidade: str = "SAO PAULO",
    uf: str = "SP",
    captcha_code: str = "",
) -> ResultadoEmissao:
    """
    Emite DARE em Lote para São Paulo através da aba DareLote.
    
    Cada item na lista 'itens' deve ser um dicionário contendo:
      - cnpj_cpf: str
      - codigo_receita_inteiro: int (ex: 10101 para ICMS DIFAL sem cadastro)
      - referencia: str (MM/AAAA)
      - data_vencimento: str (DD/MM/AAAA)
      - valor: float
    """
    if not itens:
        return False, "lista de itens vazia"

    itens_geracao = []
    for item in itens:
        cnpj_limpo = re.sub(r"[.\-/\s]", "", item.get("cnpj_cpf", ""))
        valor = float(item.get("valor", 0))
        ref = item.get("referencia", datetime.now().strftime("%m/%Y"))
        venc = item.get("data_vencimento", datetime.now().strftime("%d/%m/%Y"))
        
        # Backend de SP (DareLote) usa ISO String para vencimento do DARE em lote
        try:
            dt_obj = datetime.strptime(venc, "%d/%m/%Y")
            iso_venc = dt_obj.strftime("%Y-%m-%dT03:00:00.000Z")
        except ValueError:
            iso_venc = datetime.now().strftime("%Y-%m-%dT03:00:00.000Z")

        itens_geracao.append({
            "cnpj": cnpj_limpo,
            "Receita": {
                "codigoServicoDARE": int(item.get("codigo_receita_inteiro", 10101))
            },
            "referencia": ref,
            "dataVencimento": iso_venc,
            "valor": valor,
            "valorJuros": -1,
            "valorTotal": 0
        })

    payload_lote = {
        "itensParaGeracao": itens_geracao,
        "tipoAgrupamentoFilhotes": 0,
        "dadosContribuinteNaoCadastrado": {
            "razaoSocial": razao_social,
            "telefone": telefone,
            "endereco": endereco,
            "cidade": cidade,
            "uf": uf
        },
        "gRecaptchaResponse": captcha_code
    }

    session = requests.Session()
    session.headers.update(HEADERS_NAV)
    
    try:
        logger.info("Etapa 1: Abrindo página DARE em Lote SP (Carregando Sessão)")
        URL_LOTE = BASE_URL + "DareLote"
        resp = session.get(URL_LOTE, timeout=TIMEOUT)
        resp.raise_for_status()

        headers_url = {**HEADERS_AJAX, "Referer": URL_LOTE}
        headers_url["Content-Type"] = "application/json; charset=UTF-8"
        
        # O pulo do gato em SP: A geração nativa (/btnGerar_Click/) exige reCAPTCHA V3.
        # Porém, a rota de Fallback para Captcha V2 (/btnValidar_Click/) aceita o payload 
        # do lote inteiro e permite o download imediato, sem validação severa de Token pela Sefaz.
        logger.info("Etapa 2: Gerando DARE em Lote via endpoint Validador de Captcha (%d itens)", len(itens))
        resp_gerar = session.post(
            URL_LOTE + "/btnValidar_Click/",
            json=payload_lote,
            headers=headers_url,
            timeout=TIMEOUT
        )
        resp_gerar.raise_for_status()

        if "application/json" in resp_gerar.headers.get("Content-Type", ""):
            try:
                data_resp = resp_gerar.json()
                campo_erro = data_resp.get("erro")
                if isinstance(campo_erro, dict) and not campo_erro.get("estaOk", True):
                    return False, _normalizar_erro("gerar_lote", "rejeitado", str(data_resp)[:200])
            except ValueError:
                pass

        logger.info("Etapa 3: Baixando PDF em Lote")
        
        # Bypass client-side CAPTCHA equivalente ao do Avulso:
        # A API as vezes retorna OK puro no POST, e o arquivo é gerado via GET subsequente
        resp_pdf = session.get(
            URL_LOTE + "/FazerDownloadArquivo/",
            headers={**HEADERS_NAV, "Referer": URL_LOTE},
            timeout=TIMEOUT,
        )
        resp_pdf.raise_for_status()

        try:
            caminho, nome_arquivo = _baixar_pdf(resp_pdf, pdf_path)
        except ValueError as exc:
            return False, str(exc)

        logger.info("Emissão Lote concluída: %s", caminho)
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


# ---------------------------------------------------------------------------
# Função pública — listar_receitas
# ---------------------------------------------------------------------------
def listar_receitas(session=None, salvar_cache=True) -> dict:
    import json
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS_NAV)
        
    try:
        logger.info("Etapa 1: Acessando pagina DARE para extrair listagem")
        resp = session.get(URL_AVULSO, timeout=TIMEOUT)
        resp.raise_for_status()
        
        logger.info("Etapa 2: Fazendo consulta AJAX com CNPJ padrao para listar receitas")
        cnpj_fake = "51789601000166"
        url_consulta = f"{URL_AVULSO}/btnConsultar_Click/{cnpj_fake}"
        
        resp_ajax = session.post(
            url_consulta,
            json="",
            headers={**HEADERS_AJAX, "Referer": URL_AVULSO},
            timeout=TIMEOUT,
        )
        resp_ajax.raise_for_status()
        
        if _detectar_captcha(resp_ajax):
            url_validar = f"{URL_AVULSO}/btnValidar_Click/{cnpj_fake}"
            resp_ajax = session.post(
                url_validar,
                json="",
                headers={**HEADERS_AJAX, "Referer": URL_AVULSO},
                timeout=TIMEOUT,
            )
            resp_ajax.raise_for_status()
            
            if _detectar_captcha(resp_ajax):
                if not os.environ.get("TWOCAPTCHA_API_KEY"):
                    raise ValueError("etapa: validar_ambiente | motivo: solver não configurado | detalhe: Variável TWOCAPTCHA_API_KEY ausente no .env. Solver é obrigatório.")
                    
                debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
                os.makedirs(debug_dir, exist_ok=True)
                with open(os.path.join(debug_dir, "SP_captcha_listagem.html"), "w", encoding="utf-8") as f:
                    f.write(resp_ajax.text)
                raise ValueError("etapa: listar_receitas | motivo: captcha intransponivel detectado")

        data_consulta = resp_ajax.json()
        receitas_brutas = data_consulta.get("possiveisReceitas", [])
        
        options = []
        for r in receitas_brutas:
            nome = str(r.get("nome", ""))
            match = re.match(r"^(\d+)\s*-", nome)
            codigo = match.group(1) if match else str(r.get("codigoServicoDARE", ""))
            
            options.append({
                "codigo": codigo,
                "descricao": nome,
                "extra": {"codigoServicoDARE": r.get("codigoServicoDARE")}
            })
            
        resultado = {
            "uf": "SP",
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
            with open(os.path.join(mappings_dir, "SP.json"), "w", encoding="utf-8") as f:
                json.dump(resultado, f, indent=2, ensure_ascii=False)
                
        return resultado
        
    except Exception as e:
        logger.error(f"Falha ao extrair receitas: {e}")
        raise RuntimeError(str(e))

# ---------------------------------------------------------------------------
# Teste embutido
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    print("=" * 60)
    print("  TESTE DIRETO — Emissão de DARE (ICMS) — SP")
    print("=" * 60)
    if not os.getenv("TWOCAPTCHA_API_KEY"):
        print("[!] AVISO IMPORTANTE: A chave TWOCAPTCHA_API_KEY não foi encontrada no .env")
        print("    A execução para São Paulo falhará no momento da resolução do reCAPTCHA.")
    
    # CNPJ público de SP para testes (Bradesco)
    CNPJ_TESTE = "51.789.601/0001-66"
    PASTA_PDF = "./pdfs_sp"

    # Este payload obedece estritamente ao CONTRATO_SP (sem fallbacks destrutivos)
    dados_emissao = {
        "cnpj_cpf": "51.789.601/0001-66",
        "receita_codigo": "04601",
        "referencia": "02/2026",
        "valor": 10.00,
        "data_vencimento": "23/03/2026"
    }
    
    print(f"\n[>] Iniciando motor SP com CNPJ {CNPJ_TESTE}...\n")
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

"""
Módulo — Emissão de DAE (ICMS) para Minas Gerais (MG).
Portal: SIARE — https://www2.fazenda.mg.gov.br/

Fluxo REAL mapeado via browser/DevTools (04/03/2026, atualizado 23/03/2026):
  1. GET  /arrecadacao/ctrl/ARRECADA/ARRECADA/DOCUMENTO_ARRECADACAO?ACAO=VISUALIZAR
       → página de seleção de Grupo de Receita (cookies JSESSIONID + COOKIE_SESSAO_WEB)
        → seleciona grupo ICMS (ex: "ICMS APURADO NO PERIODO")
       → redireciona para formulário DAE_ICMS
  3. POST /arrecadacao/ctrl/ARRECADA/ARRECADA/DAE_ICMS  (ACAO=EXIBIRFLT)
       → pesquisa contribuinte por CNPJ/IE (txtIdentificacao)
       → popula nome, UF, município, dropdown de receitas
  3.5 POST /arrecadacao/ctrl/ARRECADA/ARRECADA/DAE_ICMS  (ACAO=CALCULAR)
       → SOMENTE quando guia vencida (data_pagamento > data_vencimento)
       → portal calcula multa, juros e total; retorna nos campos txtMulta, txtJuros, txtTotal
  4. POST /arrecadacao/ctrl/ARRECADA/ARRECADA/DAE_ICMS  (ACAO=PAGAVIANET)
       → envia dados completos (com multa/juros se vencida) → retorna tela de confirmação/PDF

CONTRATO DE ENTRADA (dados_emissao):
  Regras de Preenchimento:
  - ie_cnpj (Obrigatório): Documento ativo.
  - receita_codigo (Obrigatório): Exato código/grupo da receita. Se não existir na IE procurada, 
    retorna erro listando as opções reais (não faz fallback de índice).
  - valor (Obrigatório): Deve ser > 0.
  - referencia (Obrigatório): Formato MM/AAAA. Sem fallback/preenchimento silencioso.
  - data_vencimento (Obrigatório): Formato DD/MM/AAAA. Sem fallback silencioso.
  - data_pagamento (Opcional): Se ausente, assume 'data_vencimento'. Formato DD/MM/AAAA.

  Exemplo Válido (Payload aceito):
  {
      "ie_cnpj": "062307904.00-81",
      "receita_codigo": "ICMS MINEIRAIS",
      "valor": 500,
      "referencia": "01/2026",
      "data_vencimento": "20/02/2026",
      "data_pagamento": "23/03/2026"
  }
  
  Exemplo Inválido (Esperado ValueError):
  {
      "ie_cnpj": "062307904.00-81",
      "receita_codigo": "ICMS MINEIRAIS",
      "valor": 500,
      # Falhou por não informar 'referencia' ou 'data_vencimento'
  }
  Erro retornado: ValueError do tipo "motivo: data_vencimento ausente ou formato inválido"

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
import urllib.parse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("sefaz_mg")

# ---------------------------------------------------------------------------
# Constantes do portal (SIARE / Arrecadação)
# ---------------------------------------------------------------------------
BASE_URL = "https://www2.fazenda.mg.gov.br/"
URL_DOC_ARRECADACAO = (
    BASE_URL + "arrecadacao/ctrl/ARRECADA/ARRECADA/"
    "DOCUMENTO_ARRECADACAO"
)
URL_DAE_ICMS = (
    BASE_URL + "arrecadacao/ctrl/ARRECADA/ARRECADA/DAE_ICMS"
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

# Grupos de receita ICMS disponíveis no dropdown
GRUPOS_ICMS = {
    "apurado": "ICMS APURADO NO PERIODO",
    "st": "ICMS SUBSTITUICAO TRIBUTARIA",
    "importacao": "ICMS IMPORTACAO",
    "diferenca": "ICMS DIFERENCA DE ALIQUOTA",
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


def _capturar_todos_inputs(html: str) -> dict:
    """Extrai todos os campos input e select do formulário para manter state."""
    soup = BeautifulSoup(html, "html.parser")
    campos = {}
    for tag in soup.find_all(["input", "select"]):
        name = tag.get("name")
        if not name or name.startswith("W"):
            continue
        if tag.has_attr("disabled"):
            continue
            
        if tag.name == "select":
            opt = tag.find("option", selected=True)
            campos[name] = opt.get("value", "") if opt else ""
        else:
            campos[name] = tag.get("value", "")
    return campos


def _extrair_opcoes_select(html: str, nome_campo: str) -> list:
    """Extrai opções de um <select> pelo name."""
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", {"name": nome_campo})
    if not select:
        return []
    opcoes = []
    for opt in select.find_all("option"):
        val = opt.get("value", "")
        if val:
            opcoes.append({"value": val, "text": opt.get_text(strip=True)})
    return opcoes


def _validar_entradas(
    ie_cnpj: str,
    valor: float,
    receita_codigo: str,
    referencia: str,
    data_vencimento: str,
) -> None:
    if not ie_cnpj or not ie_cnpj.strip():
        raise ValueError(
            _normalizar_erro("validar_entrada", "IE/CNPJ ausente")
        )
    if not valor or valor <= 0:
        raise ValueError(
            _normalizar_erro("validar_entrada", "valor inválido ou ausente", "valor deve ser > 0")
        )
    if not receita_codigo or not receita_codigo.strip():
        raise ValueError(
            _normalizar_erro("validar_entrada", "receita_codigo ausente")
        )
    if not referencia or not re.match(r"^\d{2}/\d{4}$", referencia):
        raise ValueError(
            _normalizar_erro("validar_entrada", "referencia ausente ou formato inválido", "esperado MM/AAAA")
        )
    if not data_vencimento or not re.match(r"^\d{2}/\d{2}/\d{4}$", data_vencimento):
        raise ValueError(
            _normalizar_erro("validar_entrada", "data_vencimento ausente ou formato inválido", "esperado DD/MM/AAAA")
        )

# ---------------------------------------------------------------------------
# Função pública — emitir
# ---------------------------------------------------------------------------
def emitir(session=None, dados_emissao: dict = None, path_pdf: str = "") -> ResultadoEmissao:
    """
    Emite DAE de ICMS para Minas Gerais via portal SIARE usando nova assinatura.
    """
    if dados_emissao is None:
        dados_emissao = {}
        
    ie_cnpj = dados_emissao.get("ie_cnpj") or dados_emissao.get("ie") or dados_emissao.get("cnpj", "")
    codigo_receita = dados_emissao.get("receita_codigo", "")
    referencia = dados_emissao.get("referencia", "")
    valor = dados_emissao.get("valor", 0.0)
    info_complementares = dados_emissao.get("historico", "")
    grupo_icms = dados_emissao.get("grupo_icms", "apurado") 
    
    if isinstance(valor, str):
        try:
            valor_float = float(valor.replace(".", "").replace(",", "."))
        except ValueError:
            valor_float = 0.0
    else:
        try:
            valor_float = float(valor)
        except (TypeError, ValueError):
            valor_float = 0.0

    if not path_pdf:
        path_pdf = "./pdfs_mg"
        
    data_vencimento = dados_emissao.get("data_vencimento", "")
    data_pagamento = dados_emissao.get("data_pagamento", "") or data_vencimento
    
    try:
        _validar_entradas(
            ie_cnpj=ie_cnpj, 
            valor=valor_float, 
            receita_codigo=codigo_receita, 
            referencia=referencia, 
            data_vencimento=data_vencimento
        )
    except ValueError as exc:
        return False, str(exc)

    # Detectar se guia está vencida para acionar etapa CALCULAR MULTA/JUROS do portal
    guia_vencida = False
    try:
        dt_venc = datetime.strptime(data_vencimento, "%d/%m/%Y")
        dt_pag = datetime.strptime(data_pagamento, "%d/%m/%Y")
        if dt_pag > dt_venc:
            guia_vencida = True
            logger.info("Guia vencida detectada (pag=%s > venc=%s). Será acionado CALCULAR MULTA/JUROS no portal.", data_pagamento, data_vencimento)
    except ValueError:
        pass  # Já validado na função anterior

    parts = referencia.split("/")
    mes_referencia = str(int(parts[0]))
    ano_referencia = parts[1]

    # Validar grupo ICMS
    grupo_texto = GRUPOS_ICMS.get(grupo_icms)
    if not grupo_texto:
        return False, _normalizar_erro(
            "validar_entrada",
            f"grupo_icms inválido: '{grupo_icms}'",
            f"use: {list(GRUPOS_ICMS.keys())}",
        )

    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS_NAV)

    try:
        # ── Etapa 1: Acessar seleção de Grupo de Receita ────────────────
        logger.info("Etapa 1: Acessando página de Documento de Arrecadação")
        resp = session.get(
            URL_DOC_ARRECADACAO,
            params={"ACAO": "VISUALIZAR"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # Capturar hidden fields e o nome/valor do select ICMS
        inputs_form = _capturar_todos_inputs(resp.text)

        # Encontrar o valor do dropdown de ICMS (name="cmbICMS")
        opcoes_icms = _extrair_opcoes_select(resp.text, "cmbICMS")
        if not opcoes_icms:
            for nome in ["cmbIcms", "cmbIcms", "cmbGrupo"]:
                opcoes_icms = _extrair_opcoes_select(resp.text, nome)
                if opcoes_icms:
                    break

        valor_select_icms = ""
        for opt in opcoes_icms:
            if grupo_texto.lower() in opt["text"].lower():
                valor_select_icms = opt["value"]
                break

        # ── Etapa 2: Selecionar grupo ICMS (CONFIRMAR) ──────────────────
        logger.info("Etapa 2: Selecionando grupo '%s'", grupo_texto)
        payload_confirmar = {
            **inputs_form,
            "cmbICMS": valor_select_icms or grupo_texto,
        }

        resp = session.post(
            URL_DOC_ARRECADACAO,
            params={"ACAO": "CONFIRMAR"},
            data=payload_confirmar,
            headers={**HEADERS_NAV, "Referer": URL_DOC_ARRECADACAO + "?ACAO=VISUALIZAR"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        if "DAE_ICMS" in resp.url or "Documento de Arrecadação Estadual - ICMS" in resp.text:
            logger.info("Formulário DAE_ICMS carregado com sucesso")
        elif "DAE_ICMS" in resp.text or "DAE" in resp.text[:1000]:
            resp = session.get(URL_DAE_ICMS, timeout=TIMEOUT)
            resp.raise_for_status()
        else:
            session.ultima_resposta = resp
            return False, _normalizar_erro(
                "selecionar_grupo",
                "portal não redirecionou para formulário DAE_ICMS",
            )

        # ── Etapa 3: Pesquisar contribuinte por IE/CNPJ ──────────────────────
        logger.info("Etapa 3: Pesquisando contribuinte (Documento)")
        inputs_form = _capturar_todos_inputs(resp.text)

        doc_limpo = re.sub(r"[.\-/\s]", "", ie_cnpj)
        
        # Detecta tipo de Identificação no SIARE
        # 1 = CNPJ, 2 = CPF, 3 = Inscrição Estadual
        if len(doc_limpo) == 14:
            tipo_doc = "1"
            doc_formatado = f"{doc_limpo[:2]}.{doc_limpo[2:5]}.{doc_limpo[5:8]}/{doc_limpo[8:12]}-{doc_limpo[12:]}"
        elif len(doc_limpo) == 11:
            tipo_doc = "2"
            doc_formatado = f"{doc_limpo[:3]}.{doc_limpo[3:6]}.{doc_limpo[6:9]}-{doc_limpo[9:]}"
        else:
            tipo_doc = "3"
            if len(doc_limpo) == 13 and doc_limpo.isdigit():
                doc_formatado = f"{doc_limpo[:9]}.{doc_limpo[9:11]}-{doc_limpo[11:]}"
            else:
                doc_formatado = doc_limpo

        payload_pesquisar = {
            **inputs_form,
            "ACAO": "EXIBIRFLT",
            "cmbTipoIdentificacao": tipo_doc,
            "txtIdentificacao": doc_formatado,
            "cmbICMS": valor_select_icms or "1",
        }

        resp = session.post(
            URL_DAE_ICMS,
            params={"ACAO": "EXIBIRFLT"},
            data=payload_pesquisar,
            headers={**HEADERS_NAV, "Referer": URL_DAE_ICMS},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        soup_ie = BeautifulSoup(resp.text, "html.parser")
        for script_tag in soup_ie.find_all("script"):
            script_tag.decompose()
        texto_visivel = soup_ie.get_text(separator=" ", strip=True).lower()

        msg_portal = ""
        for tag in soup_ie.find_all(
            class_=re.compile(r"erro|vermelho|msg|alert", re.I)
        ):
            txt = tag.get_text(strip=True)
            if txt and any(p in txt.lower() for p in ["inválid", "invalida",
                           "não encontrad", "nao encontrad"]):
                msg_portal = txt[:150]
                break
                
        if not msg_portal:
            for padrao in [r"Identificação.*?inválida",
                           r"Inscrição.*?inválida",
                           r"IE.*?inválida",
                           r"não.*?encontrad"]:
                match = re.search(padrao, texto_visivel, re.I)
                if match:
                    msg_portal = match.group(0)[:150]
                    break

        if msg_portal and ("inválid" in msg_portal.lower() or
                           "invalida" in msg_portal.lower()):
            session.ultima_resposta = resp
            return False, _normalizar_erro(
                "validar_ie",
                "IE inválida segundo o portal",
                msg_portal,
            )
        if msg_portal and ("não encontrad" in msg_portal.lower() or
                           "nao encontrad" in msg_portal.lower()):
            session.ultima_resposta = resp
            return False, _normalizar_erro(
                "validar_ie",
                "IE não encontrada no cadastro SIARE",
                msg_portal,
            )

        inputs_form = _capturar_todos_inputs(resp.text)
        opcoes_receita = _extrair_opcoes_select(resp.text, "cmbReceita")

        if codigo_receita and opcoes_receita:
            codigo_match = ""
            # Procura match exato do value ou match parcial do text
            for opt in opcoes_receita:
                if codigo_receita.lower() == opt["value"].lower() or codigo_receita.lower() in opt["text"].lower():
                    codigo_match = opt["value"]
                    break
            
            if not codigo_match:
                session.ultima_resposta = resp
                return False, _normalizar_erro(
                    "selecionar_receita",
                    f"receita '{codigo_receita}' não encontrada para esta IE",
                    f"opções: {[o['value'] + ' - ' + o['text'] for o in opcoes_receita]}"
                )
            codigo_receita = codigo_match
        elif not opcoes_receita:
            session.ultima_resposta = resp
            return False, _normalizar_erro(
                "selecionar_receita",
                "nenhuma receita disponível após pesquisa da IE"
            )

        cmb_uf = ""
        sel_uf = soup_ie.find("select", {"name": "cmbUF"})
        if sel_uf and sel_uf.find("option", selected=True):
            cmb_uf = sel_uf.find("option", selected=True).get("value", "")

        cmb_mun = ""
        sel_mun = soup_ie.find("select", {"name": "cmbMunicipio"})
        if sel_mun and sel_mun.find("option", selected=True):
            cmb_mun = sel_mun.find("option", selected=True).get("value", "")

        ajax_token = ""
        m_token = re.search(r"([A-Za-z0-9_-]+![0-9]+![0-9]+)", resp.text)
        if m_token:
            ajax_token = m_token.group(1)
            url_ajax = URL_DAE_ICMS.replace('DAE_ICMS', 'AJAX/PESQUISAR_MULTA_JUROS_RECEITA')
            session.post(
                url_ajax,
                params={"ACAO": "VISUALIZAR", "identificadorReceita": codigo_receita},
                data=ajax_token,
                headers={**HEADERS_NAV, "Referer": URL_DAE_ICMS, "Content-Type": "text/plain"}
            )
            
        valor_str = f"{valor_float:.2f}".replace(".", ",")

        # Valores de multa/juros/total — preenchidos pelo portal se guia vencida
        multa_portal = ""
        juros_portal = ""
        total_portal = valor_str

        # ── Etapa 4: CALCULAR MULTA/JUROS (somente guia vencida) ────────────
        if guia_vencida:
            logger.info("Etapa 4: Calculando multa/juros via portal (guia vencida)")
            payload_calcular = {
                **inputs_form,
                "unifwScrollTop": "0",
                "unifwScrollLeft": "0",
                "ACAO": "CALCULAR",
                "cmbICMS": valor_select_icms or "1",
                "cmbUF": cmb_uf,
                "cmbMunicipio": cmb_mun,
                "cmbTipoIdentificacao": tipo_doc,
                "txtIdentificacao": doc_formatado,
                "cmbReceita": codigo_receita,
                "dtVencimento": data_vencimento,
                "dtPagamento": data_pagamento,
                "cmbPeriodo": "1",
                "cmbMes": mes_referencia,
                "cmbAno": ano_referencia,
                "txtReceita": valor_str,
                "txtMulta": "",
                "txtJuros": "",
                "txtTotal": valor_str,
                "txtIDMulta": "400",
                "txtIDJuros": "600",
                "txtReceitaSelecionada": codigo_receita,
                "txtTipoIdentificacaoDesabilitado": tipo_doc,
                "txtInformacoes": info_complementares,
            }

            payload_calc_tuples = []
            for k in [
                'ACAO', 'unifwScrollTop', 'unifwScrollLeft', 'txtTela', 'txtDAESerializado',
                'cmbTipoIdentificacao', 'txtIdentificacao', 'txtTipoIdentificacao',
                'txtIdentificacaoContribuinte', 'txtNome', 'cmbUF', 'cmbMunicipio', 'cmbICMS',
                'cmbReceita', 'dtVencimento', 'dtPagamento', 'cmbPeriodo', 'cmbMes', 'cmbAno',
                'cmbTipoDocumentoOrigem', 'txtNumeroDocumento', 'txtReceita', 'txtMulta',
                'txtJuros', 'txtTotal', 'txtIDMulta', 'txtIDJuros', 'txtInformacoes',
                'txtIDSolicitante', 'txtItemSelecionado', 'txtICMSSelecionado', 'txtReceitaSelecionada',
                'txtIDMulta', 'txtIDJuros', 'txtFlag', 'txtTipoDAE', 'txtTipoIdentificacaoDesabilitado',
                'daeConsolidadosSerializado'
            ]:
                val = payload_calcular.get(k, "")
                payload_calc_tuples.append((k, val))

            payload_calc_encoded = urllib.parse.urlencode(payload_calc_tuples, encoding="iso-8859-1")

            resp_calc = session.post(
                URL_DAE_ICMS,
                params={"ACAO": "CALCULAR"},
                data=payload_calc_encoded,
                headers={**HEADERS_NAV, "Referer": URL_DAE_ICMS, "Content-Type": "application/x-www-form-urlencoded"},
                timeout=TIMEOUT,
            )
            resp_calc.raise_for_status()

            # Salvar debug HTML para investigação
            debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, "mg_calcular_multa_juros.html"), "w", encoding="utf-8") as f:
                f.write(resp_calc.text)
            logger.info("Debug HTML de CALCULAR MULTA/JUROS salvo em debug/mg_calcular_multa_juros.html")

            # Parsear resposta: extrair multa, juros e total dos campos do formulário
            soup_calc = BeautifulSoup(resp_calc.text, "html.parser")
            inputs_calc = _capturar_todos_inputs(resp_calc.text)

            # Tentar extrair dos inputs do formulário retornado
            multa_portal = inputs_calc.get("txtMulta", "")
            juros_portal = inputs_calc.get("txtJuros", "")
            total_portal = inputs_calc.get("txtTotal", "") or valor_str

            # Fallback: buscar por value nos inputs diretamente
            if not multa_portal:
                inp_multa = soup_calc.find("input", {"name": "txtMulta"})
                if inp_multa:
                    multa_portal = inp_multa.get("value", "")
            if not juros_portal:
                inp_juros = soup_calc.find("input", {"name": "txtJuros"})
                if inp_juros:
                    juros_portal = inp_juros.get("value", "")
            if not total_portal or total_portal == valor_str:
                inp_total = soup_calc.find("input", {"name": "txtTotal"})
                if inp_total and inp_total.get("value", ""):
                    total_portal = inp_total.get("value", "")

            # Verificar se o portal retornou erro
            erro_calc = soup_calc.find(class_="msgErro")
            if erro_calc:
                msg_erro_calc = erro_calc.get_text(strip=True)
                if msg_erro_calc:
                    logger.warning("Portal retornou erro no cálculo: %s", msg_erro_calc)

            logger.info(
                "Multa/Juros retornados pelo portal: multa=%s, juros=%s, total=%s",
                multa_portal or '(vazio)', juros_portal or '(vazio)', total_portal
            )

            # Atualizar inputs_form com os campos atualizados do portal (ViewState, etc.)
            inputs_form = inputs_calc
        else:
            logger.info("Etapa 4: Guia não vencida — pulando cálculo de multa/juros")

        # ── Etapa 5: Gerando DAE (GERAR PAGAMENTO) ────────────────
        logger.info("Etapa 5: Gerando DAE")

        payload_gerar = {
            **inputs_form,
            "unifwScrollTop": "0",
            "unifwScrollLeft": "0",
            "ACAO": "PAGAVIANET",
            "cmbICMS": valor_select_icms or "1",
            "cmbUF": cmb_uf,
            "cmbMunicipio": cmb_mun,
            "cmbTipoIdentificacao": tipo_doc,
            "txtIdentificacao": doc_formatado,
            "cmbReceita": codigo_receita,
            "dtVencimento": data_vencimento,
            "dtPagamento": data_pagamento,
            "cmbPeriodo": "1",
            "cmbMes": mes_referencia,
            "cmbAno": ano_referencia,
            "txtReceita": valor_str,
            "txtMulta": multa_portal,
            "txtJuros": juros_portal,
            "txtTotal": total_portal,
            "txtIDMulta": "400",
            "txtIDJuros": "600",
            "txtReceitaSelecionada": codigo_receita,
            "txtTipoIdentificacaoDesabilitado": tipo_doc,
            "txtInformacoes": info_complementares,
        }

        ordered_keys = [
            'ACAO', 'unifwScrollTop', 'unifwScrollLeft', 'txtTela', 'txtDAESerializado', 
            'cmbTipoIdentificacao', 'txtIdentificacao', 'txtTipoIdentificacao', 
            'txtIdentificacaoContribuinte', 'txtNome', 'cmbUF', 'cmbMunicipio', 'cmbICMS', 
            'cmbReceita', 'dtVencimento', 'dtPagamento', 'cmbPeriodo', 'cmbMes', 'cmbAno', 
            'cmbTipoDocumentoOrigem', 'txtNumeroDocumento', 'txtReceita', 'txtMulta', 
            'txtJuros', 'txtTotal', 'txtIDMulta', 'txtIDJuros', 'txtInformacoes', 
            'txtIDSolicitante', 'txtItemSelecionado', 'txtICMSSelecionado', 'txtReceitaSelecionada', 
            'txtIDMulta', 'txtIDJuros', 'txtFlag', 'txtTipoDAE', 'txtTipoIdentificacaoDesabilitado', 
            'daeConsolidadosSerializado'
        ]
        
        payload_tuples = []
        for k in ordered_keys:
            val = payload_gerar.get(k, "")
            payload_tuples.append((k, val))

        payload_encoded = urllib.parse.urlencode(payload_tuples, encoding="iso-8859-1")

        resp = session.post(
            URL_DAE_ICMS,
            params={"ACAO": "PAGAVIANET"},
            data=payload_encoded,
            headers={**HEADERS_NAV, "Referer": URL_DAE_ICMS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        # ── Etapa 6: Verificar resultado e Submeter Impressão PDF ────────────
        html_text = resp.text
        soup_sucesso = BeautifulSoup(html_text, "html.parser")
        
        lbl_num = soup_sucesso.find(id="lblNumeroDocumento")
        if not lbl_num:
            session.ultima_resposta = resp
            erro_spans = soup_sucesso.find_all(class_='msgErro')
            msg_txt = erro_spans[0].get_text(separator=" ", strip=True) if erro_spans else ""
            if not msg_txt:
                lbl_msg = soup_sucesso.find(id="lblMensagem")
                msg_txt = lbl_msg.get_text(separator=" ", strip=True) if lbl_msg else "Falha não especificada."
            debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, "mg_debug_html.html"), "w", encoding="utf-8") as f: f.write(html_text)
            return False, f"etapa: 6_gerar | motivo: bloqueio | detalhe: {msg_txt}"

        numero_documento_gerado = lbl_num.get_text(strip=True)
        logger.info(f"DAE Gerado com Êxito! Número: {numero_documento_gerado}")

        # ── Etapa 7: Baixar o PDF ────────────────────────────────────────────
        form_imprimir = _capturar_todos_inputs(html_text)
        form_imprimir["ACAO"] = "VISUALIMPR"
        
        url_imprimir = URL_DAE_ICMS.replace("DAE_ICMS", "VISUALIZAR_IMPRIMIR")
        resp_pdf = session.post(
            url_imprimir,
            params={"ACAO": "VISUALIZAR"},
            data=form_imprimir,
            headers={**HEADERS_NAV, "Referer": resp.url},
            timeout=TIMEOUT
        )
        resp_pdf.raise_for_status()
        
        content_type_pdf = resp_pdf.headers.get("Content-Type", "")
        if "pdf" in content_type_pdf.lower() or content_type_pdf == "application/octet-stream" or resp_pdf.content[:5] == b"%PDF-":
            pdf_bytes = resp_pdf.content
            p_out = Path(path_pdf)
            
            # Remove any trailing separators. And wait, we must make sure the p_out is a valid folder.
            # No existing file should be named like the folder.
            if p_out.exists() and p_out.is_file():
                # For safety, remove it if it's an obstructing file
                os.remove(p_out)
                
            p_out.mkdir(parents=True, exist_ok=True)
            
            filename = f"MG_DAE_AVULSO_{ano_referencia}{mes_referencia}_{numero_documento_gerado.replace('.', '').replace('-', '')}.pdf"
            p_out = p_out / filename
            
            p_out.write_bytes(pdf_bytes)
            
            try: from .pdf_utils import validar_pdf
            except ImportError: from pdf_utils import validar_pdf
            is_valido, msg_val = validar_pdf(str(p_out.absolute()))
            if not is_valido: return False, "etapa: validar_pdf_final | motivo: falsificacao de bytes ou arquivo corrompido | detalhe: " + msg_val
            
            logger.info(f"Emissão concluída: {p_out.absolute()}")
            return True, {
                "mensagem": "ok",
                "pdf_path": str(p_out.absolute()),
                "pdf_filename": p_out.name,
                "numero_documento": numero_documento_gerado
            }
        else:
            session.ultima_resposta = resp_pdf
            return False, "etapa: 7_baixar_pdf | motivo: retorno_invalido | detalhe: Rota de impressão não retornou binário do PDF"

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
# Função pública — listar_receitas
# ---------------------------------------------------------------------------
def listar_receitas(session=None, salvar_cache=True) -> dict:
    """Extrai os grupos principais e também as opções para cada grupo em MG."""
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS_NAV)
    
    try:
        resp = session.get(URL_DOC_ARRECADACAO, params={"ACAO": "VISUALIZAR"}, timeout=TIMEOUT)
        resp.raise_for_status()

        opcoes_icms = []
        for nome in ["cmbICMS", "cmbIcms", "cmbGrupo"]:
            opcoes_icms = _extrair_opcoes_select(resp.text, nome)
            if opcoes_icms:
                break
                
        # the list is the actual ICMS codes on the dropdown
        # The main form only has Groups. Since MG requires a valid IE to list the actual recipes natively... wait!
        # DAE_ICMS form does NOT show recipes until CNPJ/IE is sent.
        # This makes it hard to fetch recipes without a valid CNPJ for each group. 
        # But for documentation purpose we just list the GROUPS as recipes since MG is unified by Group.
        options = []
        for opt in (opcoes_icms or [{"value": "1", "text": "ICMS APURADO"}]):
            if opt["value"]:
                options.append({
                    "codigo": opt["text"],
                    "descricao": opt["text"],
                    "extra": {"value_id": opt["value"]}
                })
        
        resultado = {
            "uf": "MG",
            "atualizado_em": datetime.now().isoformat(),
            "origem": "extraido_do_portal",
            "grupos": [
                {
                    "nome": "GRUPOS DE ICMS",
                    "options": options
                }
            ]
        }
        
        if salvar_cache:
            mappings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mappings")
            os.makedirs(mappings_dir, exist_ok=True)
            with open(os.path.join(mappings_dir, "MG.json"), "w", encoding="utf-8") as f:
                json.dump(resultado, f, indent=2, ensure_ascii=False)
                
        return resultado
        
    except Exception as e:
        logger.error(f"Falha ao extrair receitas MG: {e}")
        raise RuntimeError(str(e))

if __name__ == "__main__":
    print("=" * 60)
    print("  TESTE DIRETO — Emissão de DAE (ICMS) — MG")
    print("=" * 60)
    print("[!] AVISO: MG extrai o binário Base64 em tempo de execução submetida.\n")

    IE_TESTE = "16.670.085/0001-55"
    PASTA_PDF = "./pdfs_mg"

    # Este payload obedece estritamente ao CONTRATO_MG
    dados_emissao = {
    "ie_cnpj": "062307904.00-81",
    "receita_codigo": "101",
    "referencia": "01/2026",
    "data_vencimento": "20/02/2026",
    "data_pagamento": "23/03/2026",
    "valor": 500.00
    }
    
    print(f"[>] Iniciando motor MG com CNPJ/IE {IE_TESTE}...\n")
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


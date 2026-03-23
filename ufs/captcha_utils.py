"""
Utilitário compartilhado — Detecção de CAPTCHA + Snapshot de debug.

Regra rígida:
  - NÃO implementa bypass, solver, ou integração com serviços (2captcha etc.)
  - Se CAPTCHA for detectado, o módulo para e reporta claramente.

Uso padrão em cada módulo UF:
  from captcha_utils import detectar_captcha, salvar_snapshot_captcha
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import requests

logger = logging.getLogger("captcha_utils")

# ---------------------------------------------------------------------------
# Indicadores de CAPTCHA conhecidos (case-insensitive)
# ---------------------------------------------------------------------------
_INDICADORES_CAPTCHA = [
    "recaptcha",
    "g-recaptcha",
    "g-recaptcha-response",
    "hcaptcha",
    "h-captcha",
    "captcha",
    "data-sitekey",
    "grecaptcha",
    "recaptcha/api",
    "www.google.com/recaptcha",
    "www.gstatic.com/recaptcha",
    "js.hcaptcha.com",
    "captcha-container",
    "captcha_image",
    "UserCaptchaCode",
    "requiresV2",
]


def detectar_captcha(
    resp: requests.Response,
    limite_chars: int = 5000,
) -> Tuple[bool, str]:
    """
    Detecta presença de CAPTCHA na resposta HTTP.

    Verifica:
      1. Content-Type é text/html
      2. HTML contém indicadores conhecidos de CAPTCHA/reCAPTCHA/hCaptcha

    Args:
        resp: Resposta HTTP a ser inspecionada.
        limite_chars: Quantos caracteres do HTML analisar.

    Returns:
        (True, indicador_encontrado) se CAPTCHA detectado.
        (False, "") caso contrário.
    """
    content_type = resp.headers.get("Content-Type", "").lower()

    # Para APIs JSON: verificar campo requiresV2 ou mensagemCaptcha
    if "application/json" in content_type:
        try:
            data = resp.json()
            if isinstance(data, dict):
                if data.get("requiresV2", False) is True:
                    return True, "requiresV2=true (JSON)"
                captcha_msg = data.get("mensagemCaptcha", "")
                if captcha_msg:
                    return True, f"mensagemCaptcha={captcha_msg[:80]}"
        except (ValueError, AttributeError):
            pass
        return False, ""

    # Para PDF ou binários, sem risco de captcha
    if "application/pdf" in content_type:
        return False, ""
    if "application/octet-stream" in content_type:
        return False, ""

    # Para text/html ou respostas sem content-type definido
    html_lower = resp.text[:limite_chars].lower() if resp.text else ""
    if not html_lower:
        return False, ""

    for indicador in _INDICADORES_CAPTCHA:
        if indicador.lower() in html_lower:
            return True, indicador

    return False, ""


def salvar_snapshot_captcha(
    resp: requests.Response,
    uf: str,
    etapa: str,
    debug_dir: Optional[str] = None,
    limite_chars: int = 5000,
) -> str:
    """
    Salva snapshot sanitizado da resposta com CAPTCHA em debug/.

    Conteúdo do snapshot:
      - status_code, response.url, content-type
      - Primeiros 3000–5000 caracteres do HTML
      - IDs/tokens longos mascarados

    Args:
        resp: Resposta HTTP que contém CAPTCHA.
        uf: Sigla da UF (ex: "PR", "SP").
        etapa: Nome da etapa onde o CAPTCHA foi detectado.
        debug_dir: Diretório de debug (default: "debug/" relativo ao projeto).
        limite_chars: Máximo de caracteres do corpo para salvar.

    Returns:
        Caminho absoluto do arquivo debug salvo.
    """
    if debug_dir:
        pasta = Path(debug_dir)
    else:
        # Salvar em ufs/debug/ (sub-diretório do módulo)
        pasta = Path(__file__).parent / "debug"

    pasta.mkdir(parents=True, exist_ok=True)

    # Sanitizar corpo
    corpo = resp.text[:limite_chars] if resp.text else "(sem corpo)"
    corpo = _mascarar_tokens(corpo)

    conteudo = (
        f"<!-- CAPTCHA DETECTADO — UF={uf} — {etapa} — "
        f"{datetime.now().isoformat()} -->\n"
        f"<!-- status_code: {resp.status_code} -->\n"
        f"<!-- content-type: {resp.headers.get('Content-Type', 'N/A')} -->\n"
        f"<!-- url_final: {resp.url} -->\n"
        f"<!-- content-length: {len(resp.content)} bytes -->\n\n"
        f"{corpo}\n"
    )

    arquivo = pasta / f"{uf.upper()}_captcha_detectado.html"
    arquivo.write_text(conteudo, encoding="utf-8")
    logger.info("Snapshot CAPTCHA salvo em %s", arquivo)
    return str(arquivo)


def _mascarar_tokens(texto: str) -> str:
    """Mascara tokens, cookies e IDs longos no texto."""
    texto = re.sub(
        r'(token|cookie|session|csrf|auth|sitekey|secret|key)'
        r'["\'"]?\s*[:=]\s*["\'"]?'
        r'[A-Za-z0-9_\-+/=]{10,}',
        r'\1=***MASCARADO***',
        texto,
        flags=re.IGNORECASE,
    )
    return texto


def erro_captcha_padronizado(
    etapa: str,
    uf: str,
    indicador: str,
    snapshot_path: str = "",
) -> str:
    """
    Gera mensagem de erro padronizada para CAPTCHA detectado.

    Formato:
      etapa: <nome_etapa> | motivo: captcha detectado |
      detalhe: <indicador> presente na resposta (bloqueio anti-bot)

    Inclui referência ao snapshot debug quando disponível.
    """
    detalhe = f"{indicador} presente na resposta (bloqueio anti-bot)"
    if snapshot_path:
        detalhe += f" | snapshot: {snapshot_path}"
    detalhe += (
        f" | solução: configuração de Captcha Solver externo exigida "
        f"(variável TWOCAPTCHA_API_KEY)"
    )

    msg = f"etapa: {etapa} | motivo: captcha detectado | detalhe: {detalhe}"
    return msg


def checar_captcha_e_retornar(
    resp: requests.Response,
    uf: str,
    etapa: str,
    debug_dir: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Função de conveniência: detecta captcha, salva snapshot e retorna
    tupla (captcha_detectado, mensagem_erro_padronizada).

    Se captcha NÃO for detectado, retorna (False, "").
    Se captcha FOR detectado, retorna (True, msg_erro) pronto para
    uso como `return False, msg_erro` no módulo UF.
    """
    detectado, indicador = detectar_captcha(resp)
    if not detectado:
        return False, ""

    logger.warning(
        "CAPTCHA detectado na UF=%s etapa=%s indicador=%s",
        uf, etapa, indicador,
    )

    snapshot = salvar_snapshot_captcha(
        resp, uf, etapa, debug_dir=debug_dir,
    )

    msg = erro_captcha_padronizado(etapa, uf, indicador, snapshot)
    return True, msg

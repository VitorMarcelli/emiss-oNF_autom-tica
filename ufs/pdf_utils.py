import logging
import os
from pathlib import Path

logger = logging.getLogger("pdf_utils")

def validar_pdf(pdf_path: str) -> tuple[bool, str]:
    """
    Valida a integridade real de um arquivo PDF no disco.
    Verifica se existe, se o tamanho é > 0 KB, e se o magic number bate com %PDF-.
    Impede aprovação de HTMLs/erros HTTP baixados como .pdf.

    Args:
        pdf_path (str): Caminho absoluto ou relativo do PDF gerado.

    Returns:
        Tuple[bool, str]: (True, "PDF íntegro") ou (False, "motivo do erro").
    """
    logger.info("Etapa de Proteção: Validação Final do PDF...")
    path = Path(pdf_path)
    
    if not path.exists():
        motivo = "O arquivo físico não foi encontrado no disco."
        logger.error("Validação Abortada: %s", motivo)
        return False, motivo
        
    tamanho = path.stat().st_size
    if tamanho == 0:
        motivo = "O arquivo gerado retornou corrompido contendo 0 bytes (vazio)."
        logger.error("Validação Abortada: %s", motivo)
        # Opcional: remover lixo
        try: os.remove(path)
        except: pass
        return False, motivo
        
    # Verificar Assinatura (Magic Number)
    try:
        with open(path, "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                motivo = "O arquivo não inicia com a assinatura binária '%PDF-' (Possível HTML/XML de erro disfarçado de PDF)."
                logger.error("Validação Abortada: %s", motivo)
                try: os.remove(path)
                except: pass
                return False, motivo
    except Exception as e:
        motivo = f"Erro letal de I/O ao avaliar integridade binária: {str(e)}"
        logger.error("Validação Abortada: %s", motivo)
        return False, motivo
        
    logger.info("PDF Validado Transversalmente com Sucesso! Caminho: %s | Tamanho: %d bytes | Assinatura Autêntica.", pdf_path, tamanho)
    return True, "PDF oficial autenticado."

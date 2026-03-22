import logging
import time
import requests

logger = logging.getLogger("2captcha")

def resolver_recaptcha_enterprise(
    api_key: str, 
    sitekey: str, 
    url: str, 
    action: str = "",
    proxy: str = "",
    proxy_type: str = "HTTP"
) -> str:
    """
    Resolve reCAPTCHA Enterprise de forma síncrona usando a API 2Captcha.
    Para o Paraná, não temos bypass nativo (o portal exige token explícito).
    Se proxy for fornecido (ex: login:senha@123.123.123.123:3128), a API forçará o operário a usá-lo.
    """
    logger.info(f"Enviando desafio Enterprise para 2Captcha (sitekey={sitekey})...")
    payload = {
        "key": api_key,
        "method": "userrecaptcha",
        "googlekey": sitekey,
        "pageurl": url,
        "enterprise": 1,
        "version": "v3",
        "min_score": 0.9,
        "json": 1
    }
    if action:
        payload["action"] = action
    if proxy:
        payload["proxy"] = proxy
        payload["proxytype"] = proxy_type
    
    # 1. Enviar requisição para fila do solver
    resp = requests.post("http://2captcha.com/in.php", data=payload)
    resp.raise_for_status()
    data = resp.json()
    
    if data.get("status") != 1:
        raise RuntimeError(f"Falha ao enviar captcha ao 2captcha: {resp.text}")
    
    request_id = data.get("request")
    logger.info(f"CAPTCHA enviado ao 2Captcha. ID da solicitação: {request_id}. Aguardando...")
    
    # 2. Polling pela resposta (pode levar 10-60 segundos normalmente)
    for _ in range(40):
        time.sleep(5)
        resp2 = requests.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={request_id}&json=1")
        resp2.raise_for_status()
        data2 = resp2.json()
        
        if data2.get("status") == 1:
            token = data2.get("request")
            logger.info("Token CAPTCHA recebido com sucesso do 2Captcha!")
            return token
            
        if data2.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError(f"Erro na resolução do captcha via 2Captcha: {resp2.text}")
            
    raise TimeoutError("Timeout aguardando resolução do CAPTCHA (2Captcha demorou mais que 200 segundos).")

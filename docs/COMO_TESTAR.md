# GUIA RÁPIDO DE TESTES DIRETOS DO MOTOR

O projeto suporta e encoraja Testes Locais **Diretamente nos Motores (.py)** para garantir estabilidade, clareza e dispensar a necessidade de intermediários ou `.bat`.

Cada módulo foi desenhado com um `__main__` ativo e seguro, pronto para rodar instantaneamente.

---

## 🚀 1. Como Preparar o Ambiente

Se for testar **São Paulo (SP)** ou **Paraná (PR)**, você precisará configurar a resolução de Captchas:

1. Renomeie o arquivo `.env.example` para `.env` na raiz do projeto.
2. Insira sua chave real:
   ```env
   TWOCAPTCHA_API_KEY=sua_chave_real_aqui
   ```
*(Nota: GO, MG, MS e MT ignoram o Captcha e não precisam do `.env` preenchido obrigatoriamente para as emissões em teste normal).*

---

## 🏃 2. Como Executar um Teste 

Abra o Terminal (Command Prompt, PowerShell ou Terminal do VSCode), certifique-se de que está operando dentro da sua estrutura Python do projeto (venv/conda se usar) e chame o arquivo da UF desejada:

```bash
# Exemplo Teste Sefaz São Paulo:
python ufs/servicos_sefaz_sp.py

# Exemplo Teste Sefaz Mato Grosso:
python ufs/servicos_sefaz_mt.py
```

---

## 📊 3. Como Entender as Respostas 

Ao rodar, a automação isolará o teste e imprimirá de imediato no console o resultado limpo.
Se houver aprovação pelo tribunal fiscal em nuvem, você receberá:
```text
[SUCESSO] Guia Emitida!
  -> PDF Salvo em: ./pdfs_mt/DAR1_TESTE...pdf
```

Se houver violação de Regras de Negócio (ex: Sem débitos, IE inativa, data retroativa) a execução ativará o `Hard-Fail` anti-fraude:
```text
[FALHA] A automação foi interrompida:
  -> Motivo: ('etapa: emitir_dare | motivo: erro do portal | detalhe: Faltam debitos na IE')
```

---

## ⚙️ 4. Como Alterar o Payload de Teste?

Abra o próprio script do Estado (ex: `ufs/servicos_sefaz_sp.py`), desça até o rodapé (últimas linhas sob o `if __name__ == '__main__':`) e você verá a variável `dados_emissao`. 

Você é 100% incentivado a modificar esses dados para testes próprios alterando as strings:

```python
    # Este payload obedece estritamente ao CONTRATO_SP.
    dados_emissao = {
        "cnpj_cpf": "51.789.601/0001-66", # <-- Pode mudar seu CNPJ aqui
        "receita_codigo": "046",
        "referencia": "02/2026", # <-- E as competências aqui!
        "valor": 10.00,
        "data_vencimento": "23/03/2026"
    }
```
*Dica: Em caso de dúvida sobre um formato ("Data é com traço ou barra?", "Valor vai com virgula ou ponto?"), sempre consulte a Pasta `docs/CONTRATO_xx.md` daquele estado específico, ela contém todas as leis permitidas.*

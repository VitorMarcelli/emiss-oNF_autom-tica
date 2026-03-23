# README - Contratos de Entrada das UFs

Este diretório contém a documentação (SOT - Source of Truth) para o payload exigido na função `emitir` de cada motor estadual de emissão de guias (DARE/DAE/etc.).

O design do projeto não permite suposições ocultas ou *fallbacks* que mascarem a inserção incompleta ou equivocada de métricas essenciais. Toda emissão requer um Dicionário de Entrada unificado chamado `dados_emissao`.

Abaixo você encontra a matriz-resumo do comportamento de cada estado:

## Tabela-Resumo por UF

| UF | Arquivo do Motor | Tributo (Código ou Nome?) | Aceita Vencida? | Aceita Juros/Multa? | Exige Solver (Captcha)? | Observação Crítica |
|---|---|---|---|---|---|---|
| **GO** | `servicos_sefaz_go.py` | Código exato (ex: `1002`) | Sim | Automático (Portal calcula) | Não | Exige parâmetro `tipo_referencia` (`diaria` ou `complementar`). |
| **MG** | `servicos_sefaz_mg.py` | String (Match exato/parcial) | Não automatizado* | Não aplica | Não | *Limitação ICEfaces: Recálculo bloqueado para automação HTTP, gera Hard Fail na emissão vencida. |
| **MS** | `servicos_sefaz_ms.py` | Código numérico (ex: `004`) | Sim | Suporta (opcional) | Não | Portal menos verboso. A data de pagamento é assumida igual ao vencimento se não dita. |
| **MT** | `servicos_sefaz_mt.py` | Nome Exato (ex: `FETHAB - ...`) | Sim | Manual / Obrigatório na Payload | Não | Exige propriedade `acrescimos` explícita (`{"juros", "multa"}`) se for cobrar vencida. O portal não auto-calcula. |
| **PR** | `servicos_sefaz_pr.py` | Código exato Numérico | Sim | Emite o DARE cru | Sim (Requer `.env`) | Não faz match solto. Exige submissão do código da receita limpo, validando bloqueios severos no recálculo. |
| **SP** | `servicos_sefaz_sp.py` | Código (ex: `046-2`) | Sim | Automático (Bate AJAX na Fazenda) | Sim (Requer `.env`) | Executa override no seu payload aplicando penalidades diretamente do próprio Banco da Fazenda Paulista. Falha sem `TWOCAPTCHA_API_KEY` se bloqueado. |

---

## Contratos Individuais

Para detalhes absolutos de quais campos obrigatórios usar em cada estado, erros mapeados e amostras prontas em Python, consulte os links individuais:

* [Contrato GO - Goiás](CONTRATO_GO.md)
* [Contrato MG - Minas Gerais](CONTRATO_MG.md)
* [Contrato MS - Mato Grosso do Sul](CONTRATO_MS.md)
* [Contrato MT - Mato Grosso](CONTRATO_MT.md)
* [Contrato PR - Paraná](CONTRATO_PR.md)
* [Contrato SP - São Paulo](CONTRATO_SP.md)

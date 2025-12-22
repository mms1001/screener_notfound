# Cheap Rebound Scanner (Yahoo Finance)

Scanner local para identificar ações que:
- **Categoria A:** subiram **+30% nos últimos ~90 dias (63 pregões)** após terem caído **≥80% do pico de 5 anos**
- **Categoria B:** subiram **+30% nos últimos ~90 dias (63 pregões)** após baterem o **52-week low**

O output inclui:
- Data/Preço do pico (5y)
- Data/Preço do fundo (pós-pico) ou 52w low
- Data em que cruzou +30% do fundo
- Preço hoje

A ideia é gerar uma **lista grande (milhares)** para alimentar um **3º filtro** depois.

---

## Estrutura do projeto


# Avaliação do agente CineData

- Execução: 2026-10-04 21:12 UTC · REFERENCE_DATE 2026-10-01
- Status: completa
- Placar: **13/14** aprovadas
  - Aprovadas pela esperada: 13
  - Aprovadas por alternativa válida: 0
  - Reprovadas por armadilha: 0
  - Reprovadas sem correspondência: 1
- Armadilhas evitadas: 7 de 7 avaliáveis (0 sem resposta comparável)
- Requisições ao OpenRouter: 33 · do cache: 0

| ID | Aprovada | Top 1 | Recall | Bateu com | Modelo | Requisições | Tempo | Aviso |
|---|---|---|---|---|---|---|---|---|
| Q01 | sim | sim | 1.00@5 | esperada: Ranking em R$ | nvidia/nemotron-3-super-120b-a12b:free | 3 | 10.3s | - |
| Q02 | sim | sim | 1.00@5 | esperada: Receita e orçamento informados | qwen/qwen3.8-27b:free | 2 | 28.7s | - |
| Q03 | sim | sim | 1.00@5 | esperada: Com orçamento >= US$ 100 mil | qwen/qwen3.8-27b:free | 2 | 8.3s | - |
| Q04 | sim | sim | 1.00@5 | esperada: Sem anos vazados | qwen/qwen3.8-27b:free | 3 | 24.0s | - |
| Q05 | sim | sim | 1.00@5 | esperada: Com >= 100 votos em cada base | qwen/qwen3.8-27b:free | 2 | 10.5s | - |
| Q06 | sim | sim | 1.00@5 | esperada: Por ano | qwen/qwen3.8-27b:free | 2 | 7.8s | - |
| Q07 | sim | sim | 1.00@5 | esperada: Janela até hoje, contando por obra | qwen/qwen3.8-27b:free | 3 | 87.8s | - |
| Q08 | não | não | 0.80@5 | nenhuma | qwen/qwen3.8-27b:free | 2 | 39.4s | - |
| Q09 | sim | sim | 1.00@5 | esperada: Por obra | qwen/qwen3.8-27b:free | 3 | 175.8s | - |
| Q10 | sim | sim | 1.00@5 | esperada: Por gênero | qwen/qwen3.8-27b:free | 2 | 23.6s | - |
| Q11 | sim | sim | 1.00@5 | esperada: Receita e orçamento informados | qwen/qwen3.8-27b:free | 2 | 13.2s | - |
| Q12 | sim | sim | 1.00@5 | esperada: Margem agregada: SUM(lucro) / SUM(receita) | qwen/qwen3.8-27b:free | 3 | 28.4s | - |
| Q13 | sim | sim | 1.00@5 | esperada: Por obra | qwen/qwen3.8-27b:free | 2 | 47.0s | - |
| Q14 | sim | sim | 1.00@5 | esperada: Com >= 3 avaliações | qwen/qwen3.8-27b:free | 2 | 12.2s | - |

## Q01: Top 10 filmes com maior receita em R$

- Resultado: aprovada (bateu com: esperada: Ranking em R$)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: titulo → coluna do agente: titulo
- Referências: Ranking em R$: confere com o gabarito.json; Mesmo ranking em US$ (mostra a diferença): confere com o gabarito.json

SQL executada:

```sql
SELECT m.titulo, m.ano_lancamento, f.receita_brl FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id WHERE NOT f.receita_brl IS NULL ORDER BY f.receita_brl DESC, m.titulo LIMIT 10
```

Resposta do agente:

> Os 10 filmes com maior receita em R$ são:
> 
> | titulo | ano_lancamento | receita_brl |
> |--------|----------------|-------------|
> | Avatar: The Way Of Water | 2022 | 12.390.136.500,54 |
> | Avengers: Endgame | 2019 | 11.094.720.000,00 |
> | Spider-man: No Way Home | 2021 | 10.977.782.882,74 |
> | Avengers: Infinity War | 2018 | 7.190.430.847,63 |
> | Top Gun: Maverick | 2022 | 7.160.804.869,01 |
> | Barbie | 2023 | 6.856.159.007,38 |
> | The Super Mario Bros. Movie | 2023 | 6.838.413.799,10 |
> | The Lion King | 2019 | 6.227.552.146,58 |
> | Frozen II | 2019 | 6.094.028.191,32 |
> | Jurassic World: Fallen Kingdom | 2018 | 4.934.822.930,85 |
> 
> Filtros: receita_brl não nula.  
> Limitações: a contagem foi feita por registro (sk_movie_id); caso o mesmo filme apareça com mais de um ID (por exemplo, versões com datas ligeiramente diferentes), ele poderia ser contado mais de uma vez. Não foi aplicado agregação por obra (título + data de lançamento).

## Q02: Lucro médio por gênero, considerando apenas filmes com receita informada

- Resultado: aprovada (bateu com: esperada: Receita e orçamento informados)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: nome_genero → coluna do agente: nome_genero
- Referências: Receita e orçamento informados: confere com o gabarito.json; Literal: só receita informada: confere com o gabarito.json

SQL executada:

```sql
SELECT g.nome_genero, ROUND(AVG(f.lucro_brl), 2) AS lucro_medio_brl, COUNT(DISTINCT LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento) AS filmes FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id JOIN bridge_movie_genre AS bg ON bg.sk_movie_id = f.sk_movie_id JOIN dim_genres AS g ON g.sk_genre_id = bg.sk_genre_id WHERE f.receita_usd > 0 AND f.orcamento_usd > 0 GROUP BY g.sk_genre_id ORDER BY lucro_medio_brl DESC, g.nome_genero LIMIT 10
```

Resposta do agente:

> O gênero com maior lucro médio por filme é a **Ficção Científica**, com R$ 755,7 mi de lucro médio por obra.
> 
> | Gênero | Lucro médio (R$) | Filmes |
> |---|---|---|
> | Ficção Científica | R$ 755,7 mi | 134 |
> | Aventura | R$ 724,4 mi | 246 |
> | Animação | R$ 510,0 mi | 97 |
> | Fantasia | R$ 509,9 mi | 134 |
> | Família | R$ 460,0 mi | 142 |
> | Ação | R$ 457,3 mi | 379 |
> | Guerra | R$ 247,0 mi | 57 |
> | Comédia | R$ 202,6 mi | 403 |
> | Música | R$ 187,9 mi | 45 |
> | Terror | R$ 166,8 mi | 168 |
> 
> **Filtros:** lucro exige `receita_usd > 0 AND orcamento_usd > 0` (regra de negócio); valores em reais (`_brl`); contagem por obra (título + data de lançamento).

## Q03: Filmes com maior margem de lucro, entre os que possuem receita e orçamento informados

- Resultado: aprovada (bateu com: esperada: Com orçamento >= US$ 100 mil)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: titulo → coluna do agente: titulo
- Referências: Com orçamento >= US$ 100 mil: confere com o gabarito.json; Margem sobre a receita: confere com o gabarito.json

SQL executada:

```sql
SELECT m.titulo, m.ano_lancamento, ROUND(100.0 * (f.receita_usd - f.orcamento_usd) / f.receita_usd, 2) AS margem_pct, f.receita_brl, f.orcamento_brl FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id WHERE f.receita_usd > 0 AND f.orcamento_usd > 0 AND f.orcamento_usd >= 100000 ORDER BY margem_pct DESC, m.titulo LIMIT 10
```

Resposta do agente:

> **Secret Superstar (2017)** lidera com margem de 99,79%, seguido por Dragon Ball Super: Broly (99,20%) e The Farewell (98,92%).
> 
> | Filme | Ano | Margen | Receita | Orçamento |
> |---|---|---|---|---|
> | Secret Superstar | 2017 | 99,79% | R$ 436,0 mi | R$ 0,9 mi |
> | Dragon Ball Super: Broly | 2018 | 99,20% | R$ 488,6 mi | R$ 3,9 mi |
> | The Farewell | 2019 | 98,92% | R$ 86,4 mi | R$ 0,9 mi |
> | The Villainess | 2017 | 98,57% | R$ 28,7 mi | R$ 0,4 mi |
> | Terrifier 2 | 2022 | 98,34% | R$ 78,3 mi | R$ 1,3 mi |
> | Get Out | 2017 | 98,24% | R$ 791,4 mi | R$ 13,9 mi |
> | Tunnel | 2016 | 98,09% | R$ 164,1 mi | R$ 3,1 mi |
> | Winnie The Pooh: Blood And Honey | 2023 | 98,08% | R$ 26,4 mi | R$ 0,5 mi |
> | His Only Son | 2023 | 97,82% | R$ 58,8 mi | R$ 1,3 mi |
> | Wwe Money In The Bank 2019 | 2019 | 97,47% | R$ 22,7 mi | R$ 0,6 mi |
> 
> **Filtros:** receita e orçamento informados (`receita_usd > 0 AND orcamento_usd > 0`); orçamento mínimo de US$ 100 mil para excluir outliers; margem calculada em US$ (cotação por filme), valores exibidos em R$.

## Q04: Os 5 filmes mais populares

- Resultado: aprovada (bateu com: esperada: Sem anos vazados)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: titulo → coluna do agente: titulo
- Referências: Sem anos vazados: confere com o gabarito.json; Ingênua (o que um LLM tende a gerar): confere com o gabarito.json

SQL executada:

```sql
WITH base AS (SELECT m.titulo, m.data_lancamento, f.popularidade FROM dim_movies AS m JOIN fact_movies_performance AS f ON f.sk_movie_id = m.sk_movie_id WHERE NOT f.popularidade IS NULL AND NOT (f.popularidade = CAST(f.popularidade AS INTEGER) AND f.popularidade BETWEEN 1870 AND 2030)), agg AS (SELECT LOWER(TRIM(titulo)) || '|' || data_lancamento AS obra_key, MIN(titulo) AS titulo, data_lancamento, MAX(popularidade) AS popularidade FROM base GROUP BY obra_key, data_lancamento) SELECT titulo, data_lancamento AS lancamento, ROUND(popularidade, 1) AS popularidade FROM agg ORDER BY popularidade DESC LIMIT 5
```

Resposta do agente:

> Os 5 filmes mais populares do catálogo são liderados por **Blue Beetle** (2023), com índice de popularidade de 2.994,4.
> 
> | # | Filme | Lançamento | Popularidade |
> |---|-------|------------|--------------|
> | 1 | Blue Beetle | 2023-08-16 | 2.994,4 |
> | 2 | Gran Turismo | 2023-08-09 | 2.680,6 |
> | 3 | The Nun II | 2023-09-06 | 1.692,8 |
> | 4 | Meg 2: The Trench | 2023-08-02 | 1.567,3 |
> | 5 | Retribution | 2023-08-23 | 1.547,2 |
> 
> **Filtros:** índice de popularidade do TMDB (`popularidade`), com exclusão de valores que são anos vazados na coluna (ex.: 2020.0, 2019.0); contagem por obra (título + data de lançamento) para não duplicar filmes com vários ids.
> 
> **Limitações:** como a popularidade varia por id, usei o valor máximo por obra; cópias do mesmo filme com datas distintas não são agrupadas pela chave de obra.

## Q05: Filmes com maior divergência entre a nota TMDB e a nota IMDb

- Resultado: aprovada (bateu com: esperada: Com >= 100 votos em cada base)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: titulo → coluna do agente: titulo
- Referências: Com >= 100 votos em cada base: confere com o gabarito.json; Ambas as notas com votos: confere com o gabarito.json

SQL executada:

```sql
SELECT m.titulo, m.ano_lancamento, f.nota_tmdb, f.qtd_tmdb, f.nota_imdb, f.qtd_imdb, ROUND(ABS(f.nota_tmdb - f.nota_imdb), 2) AS divergencia FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id WHERE f.nota_tmdb > 0 AND f.nota_imdb > 0 AND f.qtd_tmdb >= 100 AND f.qtd_imdb >= 100 ORDER BY divergencia DESC, m.titulo LIMIT 10
```

Resposta do agente:

> O filme com a maior divergência entre a nota TMDB e a nota IMDb é **Me Against You: Mr. S's Vendetta** (2020), com diferença de 6,43 pontos.
> 
> | Filme | Ano | Nota TMDB | Votos TMDB | Nota IMDb | Votos IMDb | Divergência |
> |---|---|---|---|---|---|---|
> | Me Against You: Mr. S's Vendetta | 2020 | 8,13 | 460 | 1,7 | 511 | 6,43 |
> | 5gang: A Different Kind Of Christmas | 2019 | 8,20 | 104 | 2,0 | 3.451 | 6,20 |
> | Harry And Meghan: Escaping The Palace | 2021 | 6,76 | 153 | 2,6 | 1.485 | 4,16 |
> | Megalodon Rising | 2021 | 6,11 | 125 | 2,1 | 1.090 | 4,01 |
> | Arctic Apocalypse | 2019 | 6,20 | 165 | 2,2 | 746 | 4,00 |
> | Megaboa | 2021 | 6,50 | 436 | 2,7 | 653 | 3,80 |
> | 365 Days | 2020 | 7,06 | 8.429 | 3,3 | 110.070 | 3,76 |
> | A Nun's Curse | 2020 | 6,41 | 160 | 2,7 | 545 | 3,71 |
> | No Manches Frida 2 | 2019 | 7,99 | 810 | 4,3 | 1.414 | 3,69 |
> | The Flood | 2023 | 6,84 | 206 | 3,2 | 2.678 | 3,64 |
> 
> **Filtros:** `qtd_tmdb >= 100` e `qtd_imdb >= 100` (mínimo de votos em ambas as bases para evitar notas com amostra insuficiente); notas positivas em ambas as bases.

## Q06: Nota média IMDb por ano de lançamento

- Resultado: aprovada (bateu com: esperada: Por ano)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: ano_lancamento → coluna do agente: ano_lancamento
- Referências: Por ano: confere com o gabarito.json

SQL executada:

```sql
SELECT m.ano_lancamento, ROUND(AVG(f.nota_imdb), 2) AS nota_media_imdb, COUNT(*) AS filmes FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id WHERE f.nota_imdb > 0 GROUP BY m.ano_lancamento ORDER BY m.ano_lancamento
```

Resposta do agente:

> A nota média IMDb por ano de lançamento varia de 6,15 (2024) a 7,50 (2026), com a maioria dos anos entre 6,23 e 6,34.
> 
> | Ano | Nota média IMDb | Filmes |
> |-----|----------------|--------|
> | 2016 | 6,34 | 10.381 |
> | 2017 | 6,34 | 11.188 |
> | 2018 | 6,27 | 11.327 |
> | 2019 | 6,26 | 11.637 |
> | 2020 | 6,24 | 9.533 |
> | 2021 | 6,23 | 9.578 |
> | 2022 | 6,23 | 9.887 |
> | 2023 | 6,23 | 7.809 |
> | 2024 | 6,15 | 1.621 |
> | 2025 | 6,58 | 4 |
> | 2026 | 7,50 | 1 |
> | 2027 | 6,40 | 2 |
> | 2029 | 3,80 | 1 |
> 
> **Filtros:** `nota_imdb > 0` (exclui filmes sem nota IMDb).
> 
> **Limitações:** Os anos 2025, 2026, 2027 e 2029 têm muito poucos filmes (1 a 4), então a média não é representativa. O ano 2024 também tem poucos filmes (1.621) em comparação com os demais.

## Q07: Ator com mais participações em filmes lançados nos últimos 5 anos

- Resultado: aprovada (bateu com: esperada: Janela até hoje, contando por obra)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: ator → coluna do agente: ator
- Referências: Janela até hoje, contando por obra: confere com o gabarito.json; Janela até a última data de lançamento da base, por obra: confere com o gabarito.json; Contando por id (ingênua): confere com o gabarito.json

SQL executada:

```sql
WITH filmes AS (SELECT sk_movie_id, LOWER(TRIM(titulo)) || '|' || data_lancamento AS obra FROM dim_movies WHERE status_filme = 'Lançado' AND data_lancamento BETWEEN DATE('2026-10-01', '-5 years') AND '2026-10-01') SELECT p.nome_pessoa AS ator, COUNT(DISTINCT fi.obra) AS filmes FROM filmes AS fi JOIN bridge_movie_person AS b ON b.sk_movie_id = fi.sk_movie_id JOIN dim_people AS p ON p.sk_person_id = b.sk_person_id WHERE p.tipo_pessoa = 'Ator' AND NOT p.nome_pessoa IN (SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM JSON_EACH('["English", "French", "Spanish", "German", "Japanese", "Italian", "Korean", "Hindi", "Portuguese", "Chinese", "Mandarin", "Cantonese", "Russian", "Arabic", "Turkish", "Tamil", "Telugu", "Malayalam", "Thai", "Swedish", "Danish", "Norwegian", "Finnish", "Dutch", "Polish", "Greek", "Hebrew", "Persian", "Indonesian", "Tagalog", "Czech", "Hungarian", "Romanian", "Ukrainian", "Bengali", "Kannada", "Marathi", "United States Of America", "United Kingdom", "France", "Germany", "Japan", "India", "Canada", "Brazil", "Spain", "Italy", "South Korea", "China", "Mexico", "Australia", "Russia", "Turkey", "Argentina", "Philippines", "Hong Kong", "Taiwan", "Sweden", "Denmark", "Norway", "Finland", "Netherlands", "Belgium", "Poland", "Ireland"]')) GROUP BY p.sk_person_id ORDER BY filmes DESC, ator LIMIT 10
```

Resposta do agente:

> **Eric Roberts** é o ator com mais participações em filmes lançados nos últimos 5 anos, com 65 obras.
> 
> | Ator | Filmes |
> |---|---|
> | Eric Roberts | 65 |
> | Vennela Kishore | 38 |
> | Yogi Babu | 37 |
> | Tanikella Bharani | 29 |
> | Sunil Varma | 28 |
> | Indrans | 27 |
> | Achyuth Kumar | 26 |
> | Murali Sharma | 26 |
> | Chris Spinelli | 25 |
> | Prakash Raj | 25 |
> 
> **Filtros:** filmes com status "Lançado" lançados entre 2021-10-01 e 2026-10-01; contagem por obra (título + data de lançamento); excluídos nomes inválidos cadastrados como pessoas.

## Q08: Diretores com maior nota média (mínimo de 5 filmes)

- Resultado: reprovada (bateu com: nenhuma)
- Top 1: não · recall@5: 0.80 · valor do top 1: fora de 1%
- Chave: diretor → coluna do agente: diretor
- Referências: Nota IMDb, >= 5 filmes com nota: confere com o gabarito.json

SQL executada:

```sql
SELECT p.nome_pessoa AS diretor, ROUND(AVG(f.nota_imdb), 2) AS nota_media, COUNT(DISTINCT LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento) AS filmes FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id JOIN bridge_movie_person AS b ON b.sk_movie_id = f.sk_movie_id JOIN dim_people AS p ON p.sk_person_id = b.sk_person_id WHERE p.tipo_pessoa = 'Diretor' AND NOT p.nome_pessoa IN (SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM JSON_EACH('["English", "French", "Spanish", "German", "Japanese", "Italian", "Korean", "Hindi", "Portuguese", "Chinese", "Mandarin", "Cantonese", "Russian", "Arabic", "Turkish", "Tamil", "Telugu", "Malayalam", "Thai", "Swedish", "Danish", "Norwegian", "Finnish", "Dutch", "Polish", "Greek", "Hebrew", "Persian", "Indonesian", "Tagalog", "Czech", "Hungarian", "Romanian", "Ukrainian", "Bengali", "Kannada", "Marathi", "United States Of America", "United Kingdom", "France", "Germany", "Japan", "India", "Canada", "Brazil", "Spain", "Italy", "South Korea", "China", "Mexico", "Australia", "Russia", "Turkey", "Argentina", "Philippines", "Hong Kong", "Taiwan", "Sweden", "Denmark", "Norway", "Finland", "Netherlands", "Belgium", "Poland", "Ireland"]')) AND f.nota_imdb > 0 GROUP BY p.sk_person_id HAVING COUNT(DISTINCT LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento) >= 5 ORDER BY nota_media DESC, diretor LIMIT 10
```

Resposta do agente:

> O diretor com a maior nota média (IMDb) entre os que dirigiram pelo menos 5 filmes é **Jun Shishido**, com nota média de 9,19 em 7 filmes.
> 
> | Diretor | Nota média (IMDb) | Filmes |
> |---|---|---|
> | Jun Shishido | 9,19 | 7 |
> | Yūichirō Hayashi | 9,19 | 7 |
> | Trevor L. Allen | 9,15 | 6 |
> | Alonso O. Lara | 9,09 | 14 |
> | Tokio Igarashi | 9,00 | 5 |
> | Erlik | 8,95 | 6 |
> | Stuart Webster | 8,88 | 5 |
> | Mark Fischbach | 8,83 | 6 |
> | Don Thacker | 8,70 | 5 |
> | John D. Boswell | 8,70 | 8 |
> 
> **Filtros:** nota IMDb > 0; mínimo de 5 filmes por obra (título + data de lançamento); nomes inválidos excluídos.

## Q09: Dupla ator–diretor que mais trabalhou junta

- Resultado: aprovada (bateu com: esperada: Por obra)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: ator, diretor → coluna do agente: nome_ator, nome_diretor
- Referências: Por obra: confere com o gabarito.json; Por id (ingênua): confere com o gabarito.json

SQL executada:

```sql
WITH ator_filmes AS (SELECT b.sk_movie_id, p.sk_person_id AS sk_ator, p.nome_pessoa AS nome_ator FROM bridge_movie_person AS b JOIN dim_people AS p ON p.sk_person_id = b.sk_person_id WHERE p.tipo_pessoa = 'Ator' AND NOT p.nome_pessoa IN (SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM JSON_EACH('["English", "French", "Spanish", "German", "Japanese", "Italian", "Korean", "Hindi", "Portuguese", "Chinese", "Mandarin", "Cantonese", "Russian", "Arabic", "Turkish", "Tamil", "Telugu", "Malayalam", "Thai", "Swedish", "Danish", "Norwegian", "Finnish", "Dutch", "Polish", "Greek", "Hebrew", "Persian", "Indonesian", "Tagalog", "Czech", "Hungarian", "Romanian", "Ukrainian", "Bengali", "Kannada", "Marathi", "United States Of America", "United Kingdom", "France", "Germany", "Japan", "India", "Canada", "Brazil", "Spain", "Italy", "South Korea", "China", "Mexico", "Australia", "Russia", "Turkey", "Argentina", "Philippines", "Hong Kong", "Taiwan", "Sweden", "Denmark", "Norway", "Finland", "Netherlands", "Belgium", "Poland", "Ireland"]'))), diretor_filmes AS (SELECT b.sk_movie_id, p.sk_person_id AS sk_diretor, p.nome_pessoa AS nome_diretor FROM bridge_movie_person AS b JOIN dim_people AS p ON p.sk_person_id = b.sk_person_id WHERE p.tipo_pessoa = 'Diretor' AND NOT p.nome_pessoa IN (SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM JSON_EACH('["English", "French", "Spanish", "German", "Japanese", "Italian", "Korean", "Hindi", "Portuguese", "Chinese", "Mandarin", "Cantonese", "Russian", "Arabic", "Turkish", "Tamil", "Telugu", "Malayalam", "Thai", "Swedish", "Danish", "Norwegian", "Finnish", "Dutch", "Polish", "Greek", "Hebrew", "Persian", "Indonesian", "Tagalog", "Czech", "Hungarian", "Romanian", "Ukrainian", "Bengali", "Kannada", "Marathi", "United States Of America", "United Kingdom", "France", "Germany", "Japan", "India", "Canada", "Brazil", "Spain", "Italy", "South Korea", "China", "Mexico", "Australia", "Russia", "Turkey", "Argentina", "Philippines", "Hong Kong", "Taiwan", "Sweden", "Denmark", "Norway", "Finland", "Netherlands", "Belgium", "Poland", "Ireland"]'))) SELECT a.nome_ator, d.nome_diretor, COUNT(DISTINCT LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento) AS obras FROM ator_filmes AS a JOIN diretor_filmes AS d ON d.sk_movie_id = a.sk_movie_id JOIN dim_movies AS m ON m.sk_movie_id = a.sk_movie_id WHERE a.sk_ator <> d.sk_diretor GROUP BY a.sk_ator, d.sk_diretor ORDER BY obras DESC, a.nome_ator, d.nome_diretor LIMIT 10
```

Resposta do agente:

> A dupla ator–diretor que mais trabalhou junta é **Joe Anoa'i** com **Kevin Dunn**, em 37 obras.
> 
> | Ator | Diretor | Obras |
> |------|---------|-------|
> | Joe Anoa'i | Kevin Dunn | 37 |
> | Colby Lopez | Kevin Dunn | 32 |
> | Jeff Kirkendall | Mark Polonia | 25 |
> | Vivica A. Fox | David Decoteau | 25 |
> | Allen Jones | Kevin Dunn | 23 |
> | Brock Lesnar | Kevin Dunn | 22 |
> | Adam Scherr | Kevin Dunn | 21 |
> | Ashley Fliehr | Kevin Dunn | 19 |
> | Kj Schrock | Evan Tramel | 19 |
> | Kevin Steen | Kevin Dunn | 18 |
> 
> **Filtros:** excluídos nomes inválidos (idiomas, países e gêneros cadastrados como pessoas); excluída a pessoa consigo mesma; contagem por obra (título + data de lançamento).

## Q10: Quantidade de filmes por gênero

- Resultado: aprovada (bateu com: esperada: Por gênero)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: nome_genero → coluna do agente: nome_genero
- Referências: Por gênero: confere com o gabarito.json

SQL executada:

```sql
SELECT g.nome_genero, COUNT(DISTINCT LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento) AS filmes FROM dim_movies AS m JOIN bridge_movie_genre AS bg ON bg.sk_movie_id = m.sk_movie_id JOIN dim_genres AS g ON g.sk_genre_id = bg.sk_genre_id GROUP BY g.sk_genre_id ORDER BY filmes DESC, g.nome_genero
```

Resposta do agente:

> O gênero com mais filmes no catálogo é **Drama**, com 28.064 obras.
> 
> | Gênero | Filmes |
> |---|---|
> | Drama | 28.064 |
> | Documentário | 18.055 |
> | Comédia | 15.982 |
> | Terror | 8.608 |
> | Suspense | 8.518 |
> | Romance | 6.207 |
> | Ação | 4.986 |
> | Crime | 3.888 |
> | Animação | 3.869 |
> | Filme para TV | 3.335 |
> | Ficção Científica | 3.198 |
> | Família | 3.140 |
> | Fantasia | 2.709 |
> | Mistério | 2.699 |
> | Música | 2.380 |
> | Aventura | 2.369 |
> | História | 1.987 |
> | Guerra | 801 |
> | Faroeste | 355 |
> 
> **Filtros:** catálogo inteiro (todos os status de filme); contagem por obra (título + data de lançamento).

## Q11: Produtora com maior lucro total

- Resultado: aprovada (bateu com: esperada: Receita e orçamento informados)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: nome_produtora → coluna do agente: nome_produtora
- Referências: Receita e orçamento informados: confere com o gabarito.json; Ingênua: SUM(lucro_brl) sem filtro: confere com o gabarito.json
- Valor do top 1 (checagem obrigatória): dentro de 1%

SQL executada:

```sql
SELECT c.nome_produtora, SUM(f.lucro_brl) AS lucro_total_brl, COUNT(DISTINCT LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento) AS filmes FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id JOIN bridge_movie_company AS bc ON bc.sk_movie_id = f.sk_movie_id JOIN dim_companies AS c ON c.sk_company_id = bc.sk_company_id WHERE f.receita_usd > 0 AND f.orcamento_usd > 0 AND NOT c.nome_produtora IN (SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM JSON_EACH('["English", "French", "Spanish", "German", "Japanese", "Italian", "Korean", "Hindi", "Portuguese", "Chinese", "Mandarin", "Cantonese", "Russian", "Arabic", "Turkish", "Tamil", "Telugu", "Malayalam", "Thai", "Swedish", "Danish", "Norwegian", "Finnish", "Dutch", "Polish", "Greek", "Hebrew", "Persian", "Indonesian", "Tagalog", "Czech", "Hungarian", "Romanian", "Ukrainian", "Bengali", "Kannada", "Marathi", "United States Of America", "United Kingdom", "France", "Germany", "Japan", "India", "Canada", "Brazil", "Spain", "Italy", "South Korea", "China", "Mexico", "Australia", "Russia", "Turkey", "Argentina", "Philippines", "Hong Kong", "Taiwan", "Sweden", "Denmark", "Norway", "Finland", "Netherlands", "Belgium", "Poland", "Ireland"]')) GROUP BY c.sk_company_id ORDER BY lucro_total_brl DESC, c.nome_produtora LIMIT 10
```

Resposta do agente:

> A produtora com maior lucro total é a **Marvel Studios**, com R$ 61,55 bi em 17 filmes.
> 
> | Produtora | Lucro total (R$) | Filmes |
> |---|---|---|
> | Marvel Studios | R$ 61,55 bi | 17 |
> | Universal Pictures | R$ 57,60 bi | 91 |
> | Columbia Pictures | R$ 42,93 bi | 52 |
> | Walt Disney Pictures | R$ 35,58 bi | 34 |
> | Warner Bros. Pictures | R$ 35,41 bi | 61 |
> | Pascal Pictures | R$ 25,24 bi | 9 |
> | Paramount | R$ 23,29 bi | 50 |
> | 20th Century Fox | R$ 22,60 bi | 36 |
> | New Line Cinema | R$ 16,95 bi | 33 |
> | Dc Films | R$ 15,67 bi | 12 |
> 
> **Filtros:** apenas filmes com receita e orçamento informados (`receita_usd > 0 AND orcamento_usd > 0`); moeda em reais; contagem por obra (título + data de lançamento).

## Q12: Gênero com maior margem de lucro média

- Resultado: aprovada (bateu com: esperada: Margem agregada: SUM(lucro) / SUM(receita))
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: nome_genero → coluna do agente: nome_genero
- Referências: Margem agregada: SUM(lucro) / SUM(receita): confere com o gabarito.json; Média simples da margem por filme: confere com o gabarito.json; ROI médio (lucro / orçamento): confere com o gabarito.json

SQL executada:

```sql
WITH base AS (SELECT g.sk_genre_id, g.nome_genero, f.receita_usd, f.orcamento_usd, LOWER(TRIM(m.titulo)) || '|' || m.data_lancamento AS obra FROM fact_movies_performance AS f JOIN dim_movies AS m ON m.sk_movie_id = f.sk_movie_id JOIN bridge_movie_genre AS bg ON bg.sk_movie_id = f.sk_movie_id JOIN dim_genres AS g ON g.sk_genre_id = bg.sk_genre_id WHERE f.receita_usd > 0 AND f.orcamento_usd > 0) SELECT nome_genero, ROUND(100.0 * SUM(receita_usd - orcamento_usd) / SUM(receita_usd), 2) AS margem_pct, COUNT(DISTINCT obra) AS filmes FROM base GROUP BY sk_genre_id ORDER BY margem_pct DESC, nome_genero LIMIT 10
```

Resposta do agente:

> O gênero com maior margem de lucro agregada é **Terror (Horror)**, com 75,92% de margem sobre 168 filmes.
> 
> | Gênero | Margem (%) | Filmes |
> |---|---|---|
> | Terror | 75,92 | 168 |
> | Aventura | 69,59 | 246 |
> | Animação | 69,45 | 97 |
> | Família | 68,96 | 142 |
> | Ficção Científica | 68,67 | 134 |
> | Guerra | 66,78 | 57 |
> | Música | 66,68 | 45 |
> | Ação | 66,67 | 379 |
> | Fantasia | 65,39 | 134 |
> | Comédia | 65,04 | 403 |
> 
> **Filtros:** apenas filmes com receita e orçamento informados (`receita_usd > 0 AND orcamento_usd > 0`); margem calculada em US$ (soma de receita − soma de orçamento ÷ soma de receita), conforme a regra de margem agregada.

## Q13: Filmes mais avaliados pelos usuários

- Resultado: aprovada (bateu com: esperada: Por obra)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: titulo → coluna do agente: titulo
- Referências: Por obra: confere com o gabarito.json; Por id (ingênua): confere com o gabarito.json

SQL executada:

```sql
SELECT MIN(m.titulo) AS titulo, m.data_lancamento, COUNT(*) AS avaliacoes, ROUND(AVG(r.rating), 2) AS nota_media_usuarios FROM movie_reviews AS r JOIN dim_movies AS m ON m.sk_movie_id = r.sk_movie_id GROUP BY LOWER(TRIM(m.titulo)), m.data_lancamento ORDER BY avaliacoes DESC, titulo LIMIT 10
```

Resposta do agente:

> O filme mais avaliado pelos usuários é **Die Hart 2: Die Harter**, com 155 avaliações.
> 
> | Filme | Data de lançamento | Avaliações | Nota média (usuários) |
> |---|---|---|---|
> | Die Hart 2: Die Harter | 2024-05-30 | 155 | 5,10 |
> | Die Hart: Die Harter | 2024-05-30 | 119 | 5,12 |
> | Emesis Blue | 2023-02-20 | 73 | 5,07 |
> | Emesis Blue | 2023-02-21 | 40 | 3,89 |
> | Spider-man: Lotus | 2023-08-05 | 31 | 4,99 |
> | Emesis Blue | 2023-02-17 | 30 | 5,90 |
> | Milk & Serial | 2024-08-08 | 26 | 5,05 |
> | Die Hart 2: Die Harter | 2024-05-08 | 21 | 5,27 |
> | Spider-man: Lotus | 2023-08-11 | 16 | 6,66 |
> | Caligula: The Ultimate Cut | 2023-05-17 | 15 | 5,00 |
> 
> **Filtros:** catálogo inteiro (todos os status), avaliações de usuários (`movie_reviews`).
> 
> **Limitações:** alguns filmes aparecem com mais de uma linha porque há cópias do mesmo título com datas de lançamento diferentes no banco (ex.: "Emesis Blue" em 2023-02-17, 2023-02-20 e 2023-02-21; "Die Hart 2: Die Harter" em 2024-05-08 e 2024-05-30). A chave de obra (título + data) não as agrupa, então a contagem por obra pode estar subestimada para esses casos.

## Q14: Filmes em que a nota média dos usuários mais diverge da nota IMDb

- Resultado: aprovada (bateu com: esperada: Com >= 3 avaliações)
- Top 1: sim · recall@5: 1.00 · valor do top 1: dentro de 1%
- Chave: titulo → coluna do agente: titulo
- Referências: Com >= 3 avaliações: confere com o gabarito.json; Todos os filmes avaliados: confere com o gabarito.json

SQL executada:

```sql
SELECT m.titulo, m.ano_lancamento, ROUND(AVG(r.rating), 2) AS nota_media_usuarios, f.nota_imdb, ROUND(ABS(AVG(r.rating) - f.nota_imdb), 2) AS divergencia FROM movie_reviews AS r JOIN dim_movies AS m ON m.sk_movie_id = r.sk_movie_id JOIN fact_movies_performance AS f ON f.sk_movie_id = r.sk_movie_id WHERE f.nota_imdb > 0 GROUP BY r.sk_movie_id HAVING COUNT(*) >= 3 ORDER BY divergencia DESC, m.titulo LIMIT 10
```

Resposta do agente:

> O filme com maior divergência entre a nota média dos usuários e a nota IMDb é **Bittersweet Memories: 14 Isolated Days To Make An Album** (2023), com nota de usuários 2,43 contra 9,5 da IMDb (diferença de 7,07 pontos).
> 
> | Filme | Ano | Nota usuários | Nota IMDb | Divergência |
> |---|---|---|---|---|
> | Bittersweet Memories: 14 Isolated Days To Make An Album | 2023 | 2,43 | 9,5 | 7,07 |
> | Save Ralph | 2021 | 1,37 | 8,4 | 7,03 |
> | Emesis Blue | 2023 | 1,37 | 7,9 | 6,53 |
> | One Piece Fan Letter | 2024 | 2,82 | 9,2 | 6,38 |
> | One Piece Fan Letter | 2024 | 2,97 | 9,2 | 6,23 |
> | Without A Shirt | 2016 | 2,90 | 9,1 | 6,20 |
> | The Internet And You | 2016 | 3,70 | 9,1 | 5,40 |
> | The Rose Family | 2020 | 2,43 | 7,7 | 5,27 |
> | Wc Masculino | 2017 | 0,60 | 5,8 | 5,20 |
> | Bunch Of Kunst - A Film About Sleaford Mods | 2017 | 2,23 | 7,4 | 5,17 |
> 
> **Filtros:** filmes com pelo menos 3 avaliações de usuários; nota IMDb > 0.
> 
> **Limitações:** "One Piece Fan Letter" (2024) aparece duas vezes porque o mesmo filme possui mais de um `sk_movie_id` com avaliações distintas; a contagem de avaliações foi feita por `sk_movie_id`, não por obra.

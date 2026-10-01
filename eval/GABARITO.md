# Gabarito: SQL de referência das perguntas do enunciado

Gerado por `gabarito.py` sobre o `cinerocket.db`. Data de referência: 2026-10-01. Valores em R$ usam as colunas `_brl` do banco (cotação histórica por filme).

## Q01. Top 10 filmes com maior receita em R$
*Bilheteria e Finanças*

**Decisões**

- Ordena por receita_brl, não por receita_usd: a cotação é histórica (varia de ~3,05 a ~5,84 por filme), então a ordem em R$ difere da ordem em US$.

**Resposta esperada: Ranking em R$**

```sql
SELECT m.titulo, m.ano_lancamento, f.receita_brl, f.receita_usd
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_brl > 0
ORDER BY f.receita_brl DESC
LIMIT 10
```

| titulo | ano_lancamento | receita_brl | receita_usd |
|---|---|---|---|
| Avatar: The Way Of Water | 2022 | 12,390,136,500.54 | 2,320,250,281 |
| Avengers: Endgame | 2019 | 11,094,720,000 | 2,800,000,000 |
| Spider-man: No Way Home | 2021 | 10,977,782,882.74 | 1,921,847,111 |
| Avengers: Infinity War | 2018 | 7,190,430,847.63 | 2,052,415,039 |
| Top Gun: Maverick | 2022 | 7,160,804,869.01 | 1,488,732,821 |
| Barbie | 2023 | 6,856,159,007.38 | 1,428,545,028 |
| The Super Mario Bros. Movie | 2023 | 6,838,413,799.10 | 1,355,725,263 |
| The Lion King | 2019 | 6,227,552,146.58 | 1,663,075,401 |
| Frozen Ii | 2019 | 6,094,028,191.32 | 1,450,026,933 |
| Jurassic World: Fallen Kingdom | 2018 | 4,934,822,930.85 | 1,310,466,296 |

**Variante: Mesmo ranking em US$ (mostra a diferença)**

```sql
SELECT m.titulo, f.receita_usd, f.receita_brl
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_usd > 0
ORDER BY f.receita_usd DESC
LIMIT 10
```

| titulo | receita_usd | receita_brl |
|---|---|---|
| Avengers: Endgame | 2,800,000,000 | 11,094,720,000 |
| Avatar: The Way Of Water | 2,320,250,281 | 12,390,136,500.54 |
| Avengers: Infinity War | 2,052,415,039 | 7,190,430,847.63 |
| Spider-man: No Way Home | 1,921,847,111 | 10,977,782,882.74 |
| The Lion King | 1,663,075,401 | 6,227,552,146.58 |
| Top Gun: Maverick | 1,488,732,821 | 7,160,804,869.01 |
| Frozen Ii | 1,450,026,933 | 6,094,028,191.32 |
| Barbie | 1,428,545,028 | 6,856,159,007.38 |
| The Super Mario Bros. Movie | 1,355,725,263 | 6,838,413,799.10 |
| Black Panther | 1,349,926,083 | 4,429,782,441.36 |

## Q02. Lucro médio por gênero, considerando apenas filmes com receita informada
*Bilheteria e Finanças*

**Decisões**

- Principal: exige receita E orçamento > 0. Com só a receita, lucro_brl é igual à própria receita (orçamento ausente vira lucro de 100%).
- Variante literal: apenas receita > 0, como no enunciado. Mostre as duas no README e explique a escolha.
- AVG por gênero via bridge: cada filme conta uma vez por gênero (PK da bridge).

**Resposta esperada: Receita e orçamento informados**

```sql
SELECT g.nome_genero,
       ROUND(AVG(f.lucro_brl), 2) AS lucro_medio_brl,
       ROUND(AVG(f.lucro_usd), 2) AS lucro_medio_usd,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_usd > 0 AND f.orcamento_usd > 0
GROUP BY g.nome_genero
ORDER BY lucro_medio_brl DESC
```

| nome_genero | lucro_medio_brl | lucro_medio_usd | filmes |
|---|---|---|---|
| Science Fiction | 755,742,404.17 | 183,055,334.67 | 134 |
| Adventure | 724,425,244.47 | 181,399,653.22 | 246 |
| Animation | 509,966,381.98 | 126,541,446.29 | 97 |
| Fantasy | 509,886,798.93 | 126,985,391.65 | 134 |
| Family | 460,045,371.81 | 116,663,901.69 | 142 |
| Action | 457,268,303.09 | 114,110,307.89 | 379 |
| War | 247,003,988.77 | 63,179,459.23 | 57 |
| Comedy | 202,550,286.87 | 50,932,226.53 | 403 |
| Music | 187,931,397.14 | 52,139,511.67 | 45 |
| Horror | 166,814,396.19 | 41,516,368.31 | 168 |
| History | 148,702,036.40 | 36,228,461.98 | 96 |
| Thriller | 130,135,644.37 | 32,662,809.98 | 321 |
| Mystery | 129,678,635.57 | 33,150,658.77 | 127 |
| Drama | 125,934,724.09 | 31,859,537.94 | 602 |
| Romance | 121,600,061.64 | 31,601,076.12 | 152 |
| Crime | 115,701,719.73 | 29,494,464.50 | 166 |
| Tv Movie | 450,909.26 | 310,076.00 | 3 |
| Documentary | -1,037,292.92 | -179,402.83 | 40 |
| Western | -12,604,827.19 | -626,512.64 | 11 |

**Variante: Literal: só receita informada**

```sql
SELECT g.nome_genero,
       ROUND(AVG(f.lucro_brl), 2) AS lucro_medio_brl,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_brl > 0
GROUP BY g.nome_genero
ORDER BY lucro_medio_brl DESC
```

| nome_genero | lucro_medio_brl | filmes |
|---|---|---|
| Science Fiction | 520,783,718.33 | 227 |
| Adventure | 514,678,791.39 | 393 |
| Action | 343,223,021.87 | 594 |
| Fantasy | 335,448,446.35 | 255 |
| Family | 324,648,758.33 | 264 |
| Animation | 303,517,897.37 | 239 |
| War | 194,016,703.28 | 91 |
| History | 146,826,836.61 | 171 |
| Comedy | 143,589,472.55 | 813 |
| Mystery | 119,462,433.49 | 246 |
| Music | 111,985,444.28 | 100 |
| Romance | 98,645,875.99 | 312 |
| Crime | 93,988,989.27 | 294 |
| Thriller | 92,872,345.47 | 623 |
| Horror | 92,805,277.20 | 383 |
| Drama | 84,161,852.72 | 1297 |
| Tv Movie | 9,228,474.48 | 10 |
| Documentary | 5,028,959.44 | 153 |
| Western | -3,581,373.25 | 20 |

## Q03. Filmes com maior margem de lucro, entre os que possuem receita e orçamento informados
*Bilheteria e Finanças*

**Decisões**

- Margem = (receita - orçamento) / receita. A ordem é a mesma do ROI (lucro / orçamento): as duas crescem com receita/orçamento. Muda só o número.
- O topo é dominado por orçamentos irrisórios (US$ 1, US$ 128...): erros da origem. 87 filmes com ambos informados têm algum valor < US$ 1.000. O agente deve sinalizar isso; a variante usa orçamento mínimo de US$ 100 mil.

**Resposta esperada: Margem sobre a receita**

```sql
SELECT m.titulo, f.orcamento_usd, f.receita_usd,
       ROUND(100.0 * (f.receita_usd - f.orcamento_usd) / f.receita_usd, 2) AS margem_pct
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_usd > 0 AND f.orcamento_usd > 0
ORDER BY (f.receita_usd - f.orcamento_usd) * 1.0 / f.receita_usd DESC
LIMIT 10
```

| titulo | orcamento_usd | receita_usd | margem_pct |
|---|---|---|---|
| Dad, I'm Sorry | 128 | 17,130,489 | 100.00 |
| Etlb | 50 | 1,000,000 | 100.00 |
| Jailbait | 528 | 7,436,000 | 99.99 |
| Trivikrama | 4 | 10,000 | 99.96 |
| The Good Neighbor | 105 | 94,909 | 99.89 |
| New York Masalı | 1 | 500 | 99.80 |
| Secret Superstar | 286,284 | 137,416,709 | 99.79 |
| Alive | 1 | 400 | 99.75 |
| Bad Ben | 300 | 110,000 | 99.73 |
| Bad Ben: The Mandela Effect | 300 | 110,000 | 99.73 |

**Variante: Com orçamento >= US$ 100 mil**

```sql
SELECT m.titulo, f.orcamento_usd, f.receita_usd,
       ROUND(100.0 * (f.receita_usd - f.orcamento_usd) / f.receita_usd, 2) AS margem_pct
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_usd > 0 AND f.orcamento_usd >= 100000
ORDER BY (f.receita_usd - f.orcamento_usd) * 1.0 / f.receita_usd DESC
LIMIT 10
```

| titulo | orcamento_usd | receita_usd | margem_pct |
|---|---|---|---|
| Secret Superstar | 286,284 | 137,416,709 | 99.79 |
| Dragon Ball Super: Broly | 1,000,000 | 125,002,821 | 99.20 |
| The Farewell | 250,300 | 23,076,657 | 98.92 |
| The Villainess | 125,000 | 8,737,458 | 98.57 |
| Terrifier 2 | 250,000 | 15,065,239 | 98.34 |
| Get Out | 4,500,000 | 255,407,969 | 98.24 |
| Tunnel | 1,000,000 | 52,444,295 | 98.09 |
| Winnie The Pooh: Blood And Honey | 100,000 | 5,200,000 | 98.08 |
| His Only Son | 250,000 | 11,480,048 | 97.82 |
| Wwe Money In The Bank 2019 | 140,800 | 5,556,675 | 97.47 |

## Q04. Os 5 filmes mais populares
*Popularidade e Engajamento*

**Decisões**

- 3 dos 5 primeiros na consulta ingênua têm popularidade 2020.0, 2019.0 e 2018.0: é o ano vazado de outra coluna. A principal descarta esses valores.

**Resposta esperada: Sem anos vazados**

```sql
SELECT m.titulo, m.ano_lancamento, f.popularidade
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.popularidade IS NOT NULL AND NOT (f.popularidade = CAST(f.popularidade AS INTEGER) AND f.popularidade BETWEEN 1870 AND 2030)
ORDER BY f.popularidade DESC
LIMIT 5
```

| titulo | ano_lancamento | popularidade |
|---|---|---|
| Blue Beetle | 2023 | 2,994.36 |
| Gran Turismo | 2023 | 2,680.59 |
| The Nun Ii | 2023 | 1,692.78 |
| Meg 2: The Trench | 2023 | 1,567.27 |
| Retribution | 2023 | 1,547.22 |

**Variante: Ingênua (o que um LLM tende a gerar)**

```sql
SELECT m.titulo, f.popularidade
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.popularidade IS NOT NULL
ORDER BY f.popularidade DESC
LIMIT 5
```

| titulo | popularidade |
|---|---|
| Blue Beetle | 2,994.36 |
| Gran Turismo | 2,680.59 |
| La Fellinette | 2,020.00 |
| The Fear Footage 2: Curse Of The Tape | 2,019.00 |
| Wwe Survivor Series 2018 | 2,018.00 |

## Q05. Filmes com maior divergência entre a nota TMDB e a nota IMDb
*Popularidade e Engajamento*

**Decisões**

- nota_tmdb = 0 significa 'sem votos' (36 mil filmes); exige qtd_tmdb > 0 e nota_imdb > 0. As duas notas estão na escala 0-10. qtd_imdb pode ser nula mesmo com nota_imdb preenchida.
- Sem mínimo de votos, o topo é de filmes com 1 voto. A variante exige >= 100 votos em cada base.

**Resposta esperada: Ambas as notas com votos**

```sql
SELECT m.titulo, f.nota_tmdb, f.qtd_tmdb, f.nota_imdb, f.qtd_imdb,
       ROUND(ABS(f.nota_tmdb - f.nota_imdb), 2) AS divergencia
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.qtd_tmdb > 0 AND f.nota_tmdb > 0 AND f.nota_imdb > 0
ORDER BY divergencia DESC, f.qtd_imdb DESC
LIMIT 10
```

| titulo | nota_tmdb | qtd_tmdb | nota_imdb | qtd_imdb | divergencia |
|---|---|---|---|---|---|
| Country Music: Live At The Ryman | 10.00 | 1 | 0.60 | — | 9.40 |
| Cold Little Bird | 10.00 | 1 | 0.73 | — | 9.27 |
| Alfredo | 1.00 | 1 | 10.00 | 7 | 9.00 |
| Blade And Termeh | 10.00 | 1 | 1.20 | — | 8.80 |
| The Farmer | 0.50 | 1 | 9.00 | 7 | 8.50 |
| Sketchy Times With Lilly Singh | 10.00 | 1 | 1.70 | 174 | 8.30 |
| Afraid Of The Night | 1.00 | 1 | 9.30 | 49 | 8.30 |
| Fan Club | 1.00 | 1 | 9.30 | 44 | 8.30 |
| Night At The Ark Encounter | 10.00 | 1 | 1.80 | 38 | 8.20 |
| Ali | 1.00 | 1 | 9.20 | 12 | 8.20 |

**Variante: Com >= 100 votos em cada base**

```sql
SELECT m.titulo, f.nota_tmdb, f.qtd_tmdb, f.nota_imdb, f.qtd_imdb,
       ROUND(ABS(f.nota_tmdb - f.nota_imdb), 2) AS divergencia
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.qtd_tmdb >= 100 AND f.qtd_imdb >= 100 AND f.nota_tmdb > 0
  AND f.nota_imdb > 0
ORDER BY divergencia DESC
LIMIT 10
```

| titulo | nota_tmdb | qtd_tmdb | nota_imdb | qtd_imdb | divergencia |
|---|---|---|---|---|---|
| Me Against You: Mr. S's Vendetta | 8.13 | 460 | 1.70 | 511 | 6.43 |
| 5gang: A Different Kind Of Christmas | 8.20 | 104 | 2.00 | 3451 | 6.20 |
| Harry And Meghan: Escaping The Palace | 6.76 | 153 | 2.60 | 1485 | 4.16 |
| Megalodon Rising | 6.11 | 125 | 2.10 | 1090 | 4.01 |
| Arctic Apocalypse | 6.20 | 165 | 2.20 | 746 | 4.00 |
| Megaboa | 6.50 | 436 | 2.70 | 653 | 3.80 |
| 365 Days | 7.06 | 8429 | 3.30 | 110,070 | 3.76 |
| A Nun's Curse | 6.41 | 160 | 2.70 | 545 | 3.71 |
| No Manches Frida 2 | 7.99 | 810 | 4.30 | 1414 | 3.69 |
| The Flood | 6.84 | 206 | 3.20 | 2678 | 3.64 |

## Q06. Nota média IMDb por ano de lançamento
*Popularidade e Engajamento*

**Decisões**

- Ignora nota_imdb nula ou 0. Anos >= 2025 têm poucos filmes: mostre a contagem para o usuário não confiar em médias de 1 filme.

**Resposta esperada: Por ano**

```sql
SELECT m.ano_lancamento,
       ROUND(AVG(f.nota_imdb), 2) AS nota_media_imdb,
       COUNT(*) AS filmes_com_nota
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.nota_imdb > 0 AND m.ano_lancamento IS NOT NULL
GROUP BY m.ano_lancamento
ORDER BY m.ano_lancamento
```

| ano_lancamento | nota_media_imdb | filmes_com_nota |
|---|---|---|
| 2016 | 6.34 | 10,381 |
| 2017 | 6.34 | 11,188 |
| 2018 | 6.27 | 11,327 |
| 2019 | 6.26 | 11,637 |
| 2020 | 6.24 | 9533 |
| 2021 | 6.23 | 9578 |
| 2022 | 6.23 | 9887 |
| 2023 | 6.23 | 7809 |
| 2024 | 6.15 | 1621 |
| 2025 | 6.58 | 4 |
| 2026 | 7.50 | 1 |
| 2027 | 6.40 | 2 |
| 2029 | 3.80 | 1 |

## Q07. Ator com mais participações em filmes lançados nos últimos 5 anos
*Elenco e Equipe*

**Decisões**

- Janela relativa à data atual (2026-10-01): lançamento entre hoje - 5 anos e hoje, status 'Lançado'. A variante usa a última data de lançamento da base (2026-02-19), a regra do seu projeto Databricks. Escolha uma e documente.
- O mesmo filme aparece com vários ids (221 obras, 410 ids excedentes). A principal conta por obra (título normalizado + data), como no seu projeto.

**Resposta esperada: Janela até hoje, contando por obra**

```sql
WITH filmes AS (
    SELECT sk_movie_id, LOWER(TRIM(titulo)) || '|' || data_lancamento AS obra
    FROM dim_movies
    WHERE status_filme = 'Lançado'
      AND data_lancamento BETWEEN DATE('2026-10-01', '-5 years') AND DATE('2026-10-01')
)
SELECT p.nome_pessoa AS ator, COUNT(DISTINCT fi.obra) AS filmes
FROM filmes fi
JOIN bridge_movie_person b ON b.sk_movie_id = fi.sk_movie_id
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
WHERE p.tipo_pessoa = 'Ator' AND p.nome_pessoa NOT IN (
    SELECT nome_genero FROM dim_genres
    UNION ALL SELECT value FROM json_each('["English","French","Spanish","German",
      "Japanese","Italian","Korean","Hindi","Portuguese","Chinese","Mandarin","Cantonese",
      "Russian","Arabic","Turkish","Tamil","Telugu","Malayalam","Thai","Swedish","Danish",
      "Norwegian","Finnish","Dutch","Polish","Greek","Hebrew","Persian","Indonesian",
      "Tagalog","Czech","Hungarian","Romanian","Ukrainian","Bengali","Kannada","Marathi",
      "United States Of America","United Kingdom","France","Germany","Japan","India",
      "Canada","Brazil","Spain","Italy","South Korea","China","Mexico","Australia",
      "Russia","Turkey","Argentina","Philippines","Hong Kong","Taiwan","Sweden",
      "Denmark","Norway","Finland","Netherlands","Belgium","Poland","Ireland"]')
)
GROUP BY p.sk_person_id
ORDER BY filmes DESC, ator
LIMIT 10
```

| ator | filmes |
|---|---|
| Eric Roberts | 65 |
| Vennela Kishore | 38 |
| Yogi Babu | 37 |
| Tanikella Bharani | 29 |
| Sunil Varma | 28 |
| Indrans | 27 |
| Achyuth Kumar | 26 |
| Murali Sharma | 26 |
| Chris Spinelli | 25 |
| Prakash Raj | 25 |

**Variante: Contando por id (ingênua)**

```sql
SELECT p.nome_pessoa AS ator, COUNT(*) AS filmes
FROM dim_movies m
JOIN bridge_movie_person b ON b.sk_movie_id = m.sk_movie_id
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
WHERE p.tipo_pessoa = 'Ator' AND m.status_filme = 'Lançado'
  AND m.data_lancamento BETWEEN DATE('2026-10-01', '-5 years') AND DATE('2026-10-01')
GROUP BY p.sk_person_id
ORDER BY filmes DESC, ator
LIMIT 10
```

| ator | filmes |
|---|---|
| Eric Roberts | 65 |
| Vennela Kishore | 38 |
| Ahomas Hailwuttem | 37 |
| Anton Pelizzari | 37 |
| Cameron Nichols | 37 |
| David Love | 37 |
| Jazzyjoeyjr | 37 |
| John Whinfield | 37 |
| Yogi Babu | 37 |
| Tanikella Bharani | 29 |

**Variante: Janela até a última data de lançamento da base, por obra**

```sql
WITH ref AS (
    SELECT MAX(data_lancamento) AS d FROM dim_movies
    WHERE status_filme = 'Lançado' AND data_lancamento <= DATE('2026-10-01')
), filmes AS (
    SELECT sk_movie_id, LOWER(TRIM(titulo)) || '|' || data_lancamento AS obra
    FROM dim_movies
    WHERE status_filme = 'Lançado'
      AND data_lancamento BETWEEN DATE((SELECT d FROM ref), '-5 years')
                              AND (SELECT d FROM ref)
)
SELECT p.nome_pessoa AS ator, COUNT(DISTINCT fi.obra) AS filmes
FROM filmes fi
JOIN bridge_movie_person b ON b.sk_movie_id = fi.sk_movie_id
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
WHERE p.tipo_pessoa = 'Ator' AND p.nome_pessoa NOT IN (
    SELECT nome_genero FROM dim_genres
    UNION ALL SELECT value FROM json_each('["English","French","Spanish","German",
      "Japanese","Italian","Korean","Hindi","Portuguese","Chinese","Mandarin","Cantonese",
      "Russian","Arabic","Turkish","Tamil","Telugu","Malayalam","Thai","Swedish","Danish",
      "Norwegian","Finnish","Dutch","Polish","Greek","Hebrew","Persian","Indonesian",
      "Tagalog","Czech","Hungarian","Romanian","Ukrainian","Bengali","Kannada","Marathi",
      "United States Of America","United Kingdom","France","Germany","Japan","India",
      "Canada","Brazil","Spain","Italy","South Korea","China","Mexico","Australia",
      "Russia","Turkey","Argentina","Philippines","Hong Kong","Taiwan","Sweden",
      "Denmark","Norway","Finland","Netherlands","Belgium","Poland","Ireland"]')
)
GROUP BY p.sk_person_id
ORDER BY filmes DESC, ator
LIMIT 10
```

| ator | filmes |
|---|---|
| Eric Roberts | 78 |
| Yogi Babu | 46 |
| Vennela Kishore | 44 |
| Tanikella Bharani | 35 |
| Murali Sharma | 34 |
| Michael Paré | 33 |
| Indrans | 30 |
| Sunil Varma | 30 |
| Shawn C. Phillips | 29 |
| Julie Anne Prescott | 28 |

## Q08. Diretores com maior nota média (mínimo de 5 filmes)
*Elenco e Equipe*

**Decisões**

- Nota = IMDb (a mais completa). O mínimo de 5 conta só filmes com nota, senão a média sairia de menos de 5 filmes.
- Exclui nomes inválidos ('English', 'Documentary'...), que aqui não chegam ao topo, mas aparecem em outras perguntas sobre pessoas.

**Resposta esperada: Nota IMDb, >= 5 filmes com nota**

```sql
SELECT p.nome_pessoa AS diretor,
       ROUND(AVG(f.nota_imdb), 2) AS nota_media_imdb,
       COUNT(*) AS filmes
FROM bridge_movie_person b
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
JOIN fact_movies_performance f ON f.sk_movie_id = b.sk_movie_id
WHERE p.tipo_pessoa = 'Diretor' AND f.nota_imdb > 0
  AND p.nome_pessoa NOT IN (
    SELECT nome_genero FROM dim_genres
    UNION ALL SELECT value FROM json_each('["English","French","Spanish","German",
      "Japanese","Italian","Korean","Hindi","Portuguese","Chinese","Mandarin","Cantonese",
      "Russian","Arabic","Turkish","Tamil","Telugu","Malayalam","Thai","Swedish","Danish",
      "Norwegian","Finnish","Dutch","Polish","Greek","Hebrew","Persian","Indonesian",
      "Tagalog","Czech","Hungarian","Romanian","Ukrainian","Bengali","Kannada","Marathi",
      "United States Of America","United Kingdom","France","Germany","Japan","India",
      "Canada","Brazil","Spain","Italy","South Korea","China","Mexico","Australia",
      "Russia","Turkey","Argentina","Philippines","Hong Kong","Taiwan","Sweden",
      "Denmark","Norway","Finland","Netherlands","Belgium","Poland","Ireland"]')
)
GROUP BY p.sk_person_id
HAVING COUNT(*) >= 5
ORDER BY nota_media_imdb DESC, filmes DESC
LIMIT 10
```

| diretor | nota_media_imdb | filmes |
|---|---|---|
| Scott Wozniak | 9.34 | 5 |
| Yūichirō Hayashi | 9.19 | 8 |
| Jun Shishido | 9.19 | 8 |
| Trevor L. Allen | 9.15 | 6 |
| Alonso O. Lara | 9.09 | 14 |
| Tokio Igarashi | 9.00 | 5 |
| Erlik | 8.95 | 6 |
| Stuart Webster | 8.88 | 5 |
| Mark Fischbach | 8.83 | 6 |
| John D. Boswell | 8.70 | 8 |

## Q09. Dupla ator–diretor que mais trabalhou junta
*Elenco e Equipe*

**Decisões**

- Não há coluna de papel na bridge: o papel vem de dim_people.tipo_pessoa, então a bridge_movie_person entra duas vezes (uma para o ator, outra para o diretor).
- Conta por obra: as duplas de 'Chad Payne' (31) vêm de filmes repetidos com vários ids. Sem deduplicar, o ranking muda.
- Exclui a mesma pessoa como ator e diretor de si mesma (nomes iguais).

**Resposta esperada: Por obra**

```sql
WITH obra AS (
    SELECT sk_movie_id, LOWER(TRIM(titulo)) || '|' || COALESCE(data_lancamento, '') AS obra
    FROM dim_movies
)
SELECT a.nome_pessoa AS ator, d.nome_pessoa AS diretor,
       COUNT(DISTINCT o.obra) AS filmes_juntos
FROM bridge_movie_person ba
JOIN dim_people a ON a.sk_person_id = ba.sk_person_id AND a.tipo_pessoa = 'Ator'
JOIN bridge_movie_person bd ON bd.sk_movie_id = ba.sk_movie_id
JOIN dim_people d ON d.sk_person_id = bd.sk_person_id AND d.tipo_pessoa = 'Diretor'
JOIN obra o ON o.sk_movie_id = ba.sk_movie_id
WHERE a.nome_pessoa <> d.nome_pessoa
  AND a.nome_pessoa NOT IN (
    SELECT nome_genero FROM dim_genres
    UNION ALL SELECT value FROM json_each('["English","French","Spanish","German",
      "Japanese","Italian","Korean","Hindi","Portuguese","Chinese","Mandarin","Cantonese",
      "Russian","Arabic","Turkish","Tamil","Telugu","Malayalam","Thai","Swedish","Danish",
      "Norwegian","Finnish","Dutch","Polish","Greek","Hebrew","Persian","Indonesian",
      "Tagalog","Czech","Hungarian","Romanian","Ukrainian","Bengali","Kannada","Marathi",
      "United States Of America","United Kingdom","France","Germany","Japan","India",
      "Canada","Brazil","Spain","Italy","South Korea","China","Mexico","Australia",
      "Russia","Turkey","Argentina","Philippines","Hong Kong","Taiwan","Sweden",
      "Denmark","Norway","Finland","Netherlands","Belgium","Poland","Ireland"]')
)
  AND d.nome_pessoa NOT IN (
    SELECT nome_genero FROM dim_genres
    UNION ALL SELECT value FROM json_each('["English","French","Spanish","German",
      "Japanese","Italian","Korean","Hindi","Portuguese","Chinese","Mandarin","Cantonese",
      "Russian","Arabic","Turkish","Tamil","Telugu","Malayalam","Thai","Swedish","Danish",
      "Norwegian","Finnish","Dutch","Polish","Greek","Hebrew","Persian","Indonesian",
      "Tagalog","Czech","Hungarian","Romanian","Ukrainian","Bengali","Kannada","Marathi",
      "United States Of America","United Kingdom","France","Germany","Japan","India",
      "Canada","Brazil","Spain","Italy","South Korea","China","Mexico","Australia",
      "Russia","Turkey","Argentina","Philippines","Hong Kong","Taiwan","Sweden",
      "Denmark","Norway","Finland","Netherlands","Belgium","Poland","Ireland"]')
)
GROUP BY a.sk_person_id, d.sk_person_id
ORDER BY filmes_juntos DESC, ator
LIMIT 10
```

| ator | diretor | filmes_juntos |
|---|---|---|
| Joe Anoa'i | Kevin Dunn | 37 |
| Colby Lopez | Kevin Dunn | 32 |
| Jeff Kirkendall | Mark Polonia | 25 |
| Vivica A. Fox | David Decoteau | 25 |
| Allen Jones | Kevin Dunn | 23 |
| Brock Lesnar | Kevin Dunn | 22 |
| Adam Scherr | Kevin Dunn | 21 |
| Ashley Fliehr | Kevin Dunn | 19 |
| Kj Schrock | Evan Tramel | 19 |
| Kevin Steen | Kevin Dunn | 18 |

**Variante: Por id (ingênua)**

```sql
SELECT a.nome_pessoa AS ator, d.nome_pessoa AS diretor, COUNT(*) AS filmes_juntos
FROM bridge_movie_person ba
JOIN dim_people a ON a.sk_person_id = ba.sk_person_id AND a.tipo_pessoa = 'Ator'
JOIN bridge_movie_person bd ON bd.sk_movie_id = ba.sk_movie_id
JOIN dim_people d ON d.sk_person_id = bd.sk_person_id AND d.tipo_pessoa = 'Diretor'
GROUP BY a.sk_person_id, d.sk_person_id
ORDER BY filmes_juntos DESC, ator
LIMIT 10
```

| ator | diretor | filmes_juntos |
|---|---|---|
| Joe Anoa'i | Kevin Dunn | 37 |
| Colby Lopez | Kevin Dunn | 32 |
| Ahomas Hailwuttem | Chad Payne | 31 |
| Anton Pelizzari | Chad Payne | 31 |
| Cameron Nichols | Chad Payne | 31 |
| David Love | Chad Payne | 31 |
| Jazzyjoeyjr | Chad Payne | 31 |
| John Whinfield | Chad Payne | 31 |
| Jeff Kirkendall | Mark Polonia | 25 |
| Vivica A. Fox | David Decoteau | 25 |

## Q10. Quantidade de filmes por gênero
*Gêneros e Produtoras*

**Decisões**

- Catálogo inteiro (todos os status). Um filme com 3 gêneros conta em cada um, então a soma passa do total de filmes.

**Resposta esperada: Por gênero**

```sql
SELECT g.nome_genero, COUNT(DISTINCT bg.sk_movie_id) AS filmes
FROM bridge_movie_genre bg
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
GROUP BY g.nome_genero
ORDER BY filmes DESC, g.nome_genero
```

| nome_genero | filmes |
|---|---|
| Drama | 28,086 |
| Documentary | 18,082 |
| Comedy | 16,048 |
| Horror | 8674 |
| Thriller | 8540 |
| Romance | 6209 |
| Action | 5028 |
| Animation | 3911 |
| Crime | 3902 |
| Tv Movie | 3336 |
| Science Fiction | 3218 |
| Family | 3140 |
| Fantasy | 2722 |
| Mystery | 2713 |
| Music | 2384 |
| Adventure | 2376 |
| History | 1993 |
| War | 804 |
| Western | 355 |

## Q11. Produtora com maior lucro total
*Gêneros e Produtoras*

**Decisões**

- Soma lucro_brl só de filmes com receita E orçamento > 0. Somar lucro_brl de todos inclui receitas sem custo como lucro e muda a 3ª e a 4ª posição.
- Coprodução: o lucro integral do filme conta para cada produtora.

**Resposta esperada: Receita e orçamento informados**

```sql
SELECT c.nome_produtora,
       ROUND(SUM(f.lucro_brl), 2) AS lucro_total_brl,
       ROUND(SUM(f.lucro_usd), 2) AS lucro_total_usd,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_company bc ON bc.sk_movie_id = f.sk_movie_id
JOIN dim_companies c ON c.sk_company_id = bc.sk_company_id
WHERE f.receita_usd > 0 AND f.orcamento_usd > 0 AND c.nome_produtora NOT IN (
    SELECT nome_genero FROM dim_genres
    UNION ALL SELECT value FROM json_each('["English","French","Spanish","German",
      "Japanese","Italian","Korean","Hindi","Portuguese","Chinese","Mandarin","Cantonese",
      "Russian","Arabic","Turkish","Tamil","Telugu","Malayalam","Thai","Swedish","Danish",
      "Norwegian","Finnish","Dutch","Polish","Greek","Hebrew","Persian","Indonesian",
      "Tagalog","Czech","Hungarian","Romanian","Ukrainian","Bengali","Kannada","Marathi",
      "United States Of America","United Kingdom","France","Germany","Japan","India",
      "Canada","Brazil","Spain","Italy","South Korea","China","Mexico","Australia",
      "Russia","Turkey","Argentina","Philippines","Hong Kong","Taiwan","Sweden",
      "Denmark","Norway","Finland","Netherlands","Belgium","Poland","Ireland"]')
)
GROUP BY c.sk_company_id
ORDER BY lucro_total_brl DESC
LIMIT 10
```

| nome_produtora | lucro_total_brl | lucro_total_usd | filmes |
|---|---|---|---|
| Marvel Studios | 61,553,661,048.84 | 14,897,936,776.00 | 17 |
| Universal Pictures | 57,596,246,752.43 | 13,691,646,318.00 | 91 |
| Columbia Pictures | 42,926,347,275.90 | 9,853,057,126.00 | 52 |
| Walt Disney Pictures | 35,583,520,283.85 | 9,431,052,715.00 | 34 |
| Warner Bros. Pictures | 35,408,928,736.30 | 9,490,342,370.00 | 61 |
| Pascal Pictures | 25,240,262,251.21 | 5,454,787,221.00 | 9 |
| Paramount | 23,285,364,755.82 | 5,420,774,477.00 | 50 |
| 20th Century Fox | 22,595,996,259.75 | 6,539,720,788.00 | 36 |
| New Line Cinema | 16,949,962,904.51 | 4,368,405,475.00 | 33 |
| Dc Films | 15,672,321,155.25 | 4,147,832,105.00 | 12 |

**Variante: Ingênua: SUM(lucro_brl) sem filtro**

```sql
SELECT c.nome_produtora, ROUND(SUM(f.lucro_brl), 2) AS lucro_total_brl
FROM fact_movies_performance f
JOIN bridge_movie_company bc ON bc.sk_movie_id = f.sk_movie_id
JOIN dim_companies c ON c.sk_company_id = bc.sk_company_id
GROUP BY c.sk_company_id
ORDER BY lucro_total_brl DESC
LIMIT 10
```

| nome_produtora | lucro_total_brl |
|---|---|
| Marvel Studios | 63,936,626,417.88 |
| Universal Pictures | 61,785,769,756.69 |
| Walt Disney Pictures | 42,108,311,522.57 |
| Columbia Pictures | 41,955,725,270.78 |
| Warner Bros. Pictures | 34,448,782,502.23 |
| Paramount | 26,894,444,512.78 |
| Pascal Pictures | 24,862,086,083.81 |
| 20th Century Fox | 21,381,491,636.49 |
| Illumination | 20,217,211,991.96 |
| New Line Cinema | 17,526,657,793.53 |

## Q12. Gênero com maior margem de lucro média
*Gêneros e Produtoras*

**Decisões**

- Principal: margem do gênero = SUM(lucro) / SUM(receita), com receita e orçamento > 0. É a média ponderada pela receita e não é distorcida por valores extremos.
- A média simples das margens por filme não serve: (receita - orçamento) / receita não tem piso, e um fracasso com receita de US$ 1 vale -1.000.000%. Todos os gêneros ficam negativos. A média do ROI (lucro / orçamento) explode no sentido oposto, com orçamentos de US$ 1. As duas variantes mostram isso.
- Explique a escolha no README: a pergunta é ambígua e o resultado depende da fórmula.

**Resposta esperada: Margem agregada: SUM(lucro) / SUM(receita)**

```sql
SELECT g.nome_genero,
       ROUND(100.0 * SUM(f.receita_usd - f.orcamento_usd) / SUM(f.receita_usd), 2)
           AS margem_pct,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_usd > 0 AND f.orcamento_usd > 0
GROUP BY g.nome_genero
ORDER BY margem_pct DESC
```

| nome_genero | margem_pct | filmes |
|---|---|---|
| Horror | 75.92 | 168 |
| Adventure | 69.59 | 246 |
| Animation | 69.45 | 97 |
| Family | 68.96 | 142 |
| Science Fiction | 68.67 | 134 |
| War | 66.78 | 57 |
| Music | 66.68 | 45 |
| Action | 66.67 | 379 |
| Fantasy | 65.39 | 134 |
| Comedy | 65.04 | 403 |
| Drama | 63.16 | 602 |
| Romance | 62.30 | 152 |
| Mystery | 60.36 | 127 |
| Thriller | 58.55 | 321 |
| History | 53.24 | 96 |
| Crime | 50.07 | 166 |
| Tv Movie | 29.25 | 3 |
| Western | -2.48 | 11 |
| Documentary | -16.82 | 40 |

**Variante: Média simples da margem por filme**

```sql
SELECT g.nome_genero,
       ROUND(100.0 * AVG((f.receita_usd - f.orcamento_usd) * 1.0 / f.receita_usd), 2)
           AS margem_media_pct,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_usd > 0 AND f.orcamento_usd > 0
GROUP BY g.nome_genero
ORDER BY margem_media_pct DESC
```

| nome_genero | margem_media_pct | filmes |
|---|---|---|
| War | -534.88 | 57 |
| Romance | -586.91 | 152 |
| Music | -640.95 | 45 |
| History | -826.54 | 96 |
| Animation | -845.31 | 97 |
| Action | -1,233.02 | 379 |
| Adventure | -1,313.73 | 246 |
| Family | -1,335.65 | 142 |
| Horror | -1,426.56 | 168 |
| Mystery | -1,507.55 | 127 |
| Fantasy | -1,616.99 | 134 |
| Science Fiction | -3,380.72 | 134 |
| Drama | -3,560.03 | 602 |
| Thriller | -4,242.33 | 321 |
| Tv Movie | -6,599.51 | 3 |
| Crime | -7,126.15 | 166 |
| Comedy | -14,846.63 | 403 |
| Western | -30,684.24 | 11 |
| Documentary | -152,024.25 | 40 |

**Variante: ROI médio (lucro / orçamento)**

```sql
SELECT g.nome_genero,
       ROUND(100.0 * AVG((f.receita_usd - f.orcamento_usd) * 1.0 / f.orcamento_usd), 2)
           AS roi_medio_pct,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_usd > 0 AND f.orcamento_usd > 0
GROUP BY g.nome_genero
ORDER BY roi_medio_pct DESC
```

| nome_genero | roi_medio_pct | filmes |
|---|---|---|
| Family | 94,620.34 | 142 |
| Comedy | 37,787.36 | 403 |
| Drama | 25,351.84 | 602 |
| Romance | 2,152.58 | 152 |
| Music | 1,520.05 | 45 |
| Thriller | 655.56 | 321 |
| Horror | 410.84 | 168 |
| Science Fiction | 397.55 | 134 |
| Adventure | 366.45 | 246 |
| Action | 353.54 | 379 |
| Fantasy | 316.17 | 134 |
| Crime | 308.41 | 166 |
| Documentary | 267.91 | 40 |
| Mystery | 226.68 | 127 |
| Animation | 185.55 | 97 |
| War | 160.64 | 57 |
| History | 90.84 | 96 |
| Tv Movie | 46.01 | 3 |
| Western | -34.52 | 11 |

## Q13. Filmes mais avaliados pelos usuários
*Avaliações dos Usuários*

**Decisões**

- Conta em movie_reviews. Neste banco a dim_reviews bate com movie_reviews (só 5 médias diferem no arredondamento), então qtd_avaliacoes_usuarios também serve.
- Por id, o top 10 é todo de cópias de 'Die Hart' (o mesmo filme com até 25 ids). A principal soma as avaliações por obra (título + data).
- Limitação: algumas cópias têm datas diferentes ('Emesis Blue' em 17, 20 e 21/02/2023), então a chave título + data não junta tudo. Agrupar só pelo título juntaria filmes diferentes com o mesmo nome. Vale citar no README.

**Resposta esperada: Por obra**

```sql
SELECT MIN(m.titulo) AS titulo, m.data_lancamento,
       COUNT(*) AS avaliacoes, ROUND(AVG(r.rating), 2) AS nota_media_usuarios,
       COUNT(DISTINCT m.sk_movie_id) AS ids_no_catalogo
FROM movie_reviews r
JOIN dim_movies m ON m.sk_movie_id = r.sk_movie_id
GROUP BY LOWER(TRIM(m.titulo)), m.data_lancamento
ORDER BY avaliacoes DESC, titulo
LIMIT 10
```

| titulo | data_lancamento | avaliacoes | nota_media_usuarios | ids_no_catalogo |
|---|---|---|---|---|
| Die Hart 2: Die Harter | 2024-05-30 | 155 | 5.10 | 24 |
| Die Hart: Die Harter | 2024-05-30 | 119 | 5.12 | 18 |
| Emesis Blue | 2023-02-20 | 73 | 5.07 | 17 |
| Emesis Blue | 2023-02-21 | 40 | 3.89 | 8 |
| Spider-man: Lotus | 2023-08-05 | 31 | 4.99 | 9 |
| Emesis Blue | 2023-02-17 | 30 | 5.90 | 7 |
| Milk & Serial | 2024-08-08 | 26 | 5.05 | 6 |
| Die Hart 2: Die Harter | 2024-05-08 | 21 | 5.27 | 3 |
| Spider-man: Lotus | 2023-08-11 | 16 | 6.66 | 6 |
| Caligula: The Ultimate Cut | 2023-05-17 | 15 | 5.00 | 5 |

**Variante: Por id (ingênua)**

```sql
SELECT m.titulo, COUNT(*) AS avaliacoes, ROUND(AVG(r.rating), 2) AS nota_media_usuarios
FROM movie_reviews r
JOIN dim_movies m ON m.sk_movie_id = r.sk_movie_id
GROUP BY r.sk_movie_id
ORDER BY avaliacoes DESC, m.titulo
LIMIT 10
```

| titulo | avaliacoes | nota_media_usuarios |
|---|---|---|
| Die Hart 2: Die Harter | 13 | 4.99 |
| Die Hart 2: Die Harter | 12 | 6.49 |
| Die Hart: Die Harter | 11 | 5.56 |
| Die Hart 2: Die Harter | 10 | 5.42 |
| Die Hart: Die Harter | 10 | 5.97 |
| Die Hart: Die Harter | 10 | 4.04 |
| Die Hart: Die Harter | 10 | 4.45 |
| Die Hart 2: Die Harter | 9 | 5.04 |
| Die Hart 2: Die Harter | 9 | 4.99 |
| Die Hart 2: Die Harter | 9 | 4.04 |

## Q14. Filmes em que a nota média dos usuários mais diverge da nota IMDb
*Avaliações dos Usuários*

**Decisões**

- Média dos usuários calculada de movie_reviews.rating (escala 0-10, como o IMDb). Exige nota_imdb > 0.
- Com 1 avaliação, a 'média' é uma única nota aleatória. A variante exige >= 3 avaliações.

**Resposta esperada: Todos os filmes avaliados**

```sql
WITH u AS (
    SELECT sk_movie_id, AVG(rating) AS media, COUNT(*) AS n
    FROM movie_reviews GROUP BY sk_movie_id
)
SELECT m.titulo, ROUND(u.media, 2) AS nota_media_usuarios, u.n AS avaliacoes,
       f.nota_imdb, ROUND(ABS(u.media - f.nota_imdb), 2) AS divergencia
FROM u
JOIN fact_movies_performance f ON f.sk_movie_id = u.sk_movie_id
JOIN dim_movies m ON m.sk_movie_id = u.sk_movie_id
WHERE f.nota_imdb > 0
ORDER BY divergencia DESC, u.n DESC
LIMIT 10
```

| titulo | nota_media_usuarios | avaliacoes | nota_imdb | divergencia |
|---|---|---|---|---|
| The Moon Child | 0.00 | 1 | 9.80 | 9.80 |
| Nathan For You: Finding Frances | 0.10 | 1 | 9.50 | 9.40 |
| Opus Cope: An Algorithmic Opera | 0.00 | 1 | 9.30 | 9.30 |
| Venatio | 0.10 | 1 | 9.30 | 9.20 |
| Butterfly | 0.40 | 1 | 9.60 | 9.20 |
| 702 | 0.55 | 2 | 9.70 | 9.15 |
| Ivy | 0.30 | 1 | 9.40 | 9.10 |
| Tendlya | 0.20 | 1 | 9.30 | 9.10 |
| Jaimen Hudson: From Sky To Sea | 0.20 | 1 | 9.30 | 9.10 |
| Red Dead Redemption 2 | 0.70 | 1 | 9.80 | 9.10 |

**Variante: Com >= 3 avaliações**

```sql
WITH u AS (
    SELECT sk_movie_id, AVG(rating) AS media, COUNT(*) AS n
    FROM movie_reviews GROUP BY sk_movie_id HAVING COUNT(*) >= 3
)
SELECT m.titulo, ROUND(u.media, 2) AS nota_media_usuarios, u.n AS avaliacoes,
       f.nota_imdb, ROUND(ABS(u.media - f.nota_imdb), 2) AS divergencia
FROM u
JOIN fact_movies_performance f ON f.sk_movie_id = u.sk_movie_id
JOIN dim_movies m ON m.sk_movie_id = u.sk_movie_id
WHERE f.nota_imdb > 0
ORDER BY divergencia DESC
LIMIT 10
```

| titulo | nota_media_usuarios | avaliacoes | nota_imdb | divergencia |
|---|---|---|---|---|
| Bittersweet Memories: 14 Isolated Days To Make An Album | 2.43 | 3 | 9.50 | 7.07 |
| Save Ralph | 1.37 | 3 | 8.40 | 7.03 |
| Emesis Blue | 1.37 | 3 | 7.90 | 6.53 |
| One Piece Fan Letter | 2.82 | 4 | 9.20 | 6.38 |
| One Piece Fan Letter | 2.97 | 3 | 9.20 | 6.23 |
| Without A Shirt | 2.90 | 3 | 9.10 | 6.20 |
| The Internet And You | 3.70 | 3 | 9.10 | 5.40 |
| The Rose Family | 2.43 | 3 | 7.70 | 5.27 |
| Wc Masculino | 0.60 | 3 | 5.80 | 5.20 |
| Ena: Temptation Stairway | 3.63 | 3 | 8.80 | 5.17 |

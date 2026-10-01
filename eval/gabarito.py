"""Gabarito (SQL de referência) das perguntas de exemplo da Atividade GenAI.

Executa cada consulta em modo somente leitura sobre o cinerocket.db e grava:
  - gabarito.json : pergunta, SQL, decisões e resultado esperado (conjunto de avaliação)
  - GABARITO.md   : o mesmo conteúdo em formato legível

Uso:
    python gabarito.py caminho/para/cinerocket.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Data de referência para "últimos N anos". Fixada para o gabarito ser reprodutível;
# no agente, use date('now') ou injete a data atual no prompt.
DATA_HOJE = "2026-10-01"

# Resíduos de column shift que aparecem como pessoa ou produtora (idiomas, países e
# gêneros). Mesmo problema do achado nº 4 do projeto de Engenharia de Dados.
NOMES_INVALIDOS = """(
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
)"""

# Popularidade com valor inteiro exato entre 1870 e 2030 é o ano vazado de outra
# coluna (achado nº 5 do projeto de Engenharia de Dados).
POPULARIDADE_VALIDA = (
    "NOT (f.popularidade = CAST(f.popularidade AS INTEGER) "
    "AND f.popularidade BETWEEN 1870 AND 2030)"
)

# Lucro e margem só são confiáveis com receita E orçamento informados: quando falta um
# deles, lucro_usd/lucro_brl NÃO é nulo (vira a própria receita ou -orçamento).
FINANCEIRO_COMPLETO = "f.receita_usd > 0 AND f.orcamento_usd > 0"


@dataclass
class Consulta:
    titulo: str
    sql: str


@dataclass
class Pergunta:
    id: str
    categoria: str
    pergunta: str
    decisoes: list[str]
    principal: Consulta
    variantes: list[Consulta] = field(default_factory=list)


PERGUNTAS: list[Pergunta] = [
    # ------------------------------------------------------------------ Bilheteria
    Pergunta(
        "Q01", "Bilheteria e Finanças", "Top 10 filmes com maior receita em R$",
        [
            "Ordena por receita_brl, não por receita_usd: a cotação é histórica (varia de "
            "~3,05 a ~5,84 por filme), então a ordem em R$ difere da ordem em US$.",
        ],
        Consulta("Ranking em R$", """
SELECT m.titulo, m.ano_lancamento, f.receita_brl, f.receita_usd
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_brl > 0
ORDER BY f.receita_brl DESC
LIMIT 10"""),
        [Consulta("Mesmo ranking em US$ (mostra a diferença)", """
SELECT m.titulo, f.receita_usd, f.receita_brl
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_usd > 0
ORDER BY f.receita_usd DESC
LIMIT 10""")],
    ),
    Pergunta(
        "Q02", "Bilheteria e Finanças",
        "Lucro médio por gênero, considerando apenas filmes com receita informada",
        [
            "Principal: exige receita E orçamento > 0. Com só a receita, lucro_brl é igual "
            "à própria receita (orçamento ausente vira lucro de 100%).",
            "Variante literal: apenas receita > 0, como no enunciado. Mostre as duas no "
            "README e explique a escolha.",
            "AVG por gênero via bridge: cada filme conta uma vez por gênero (PK da bridge).",
        ],
        Consulta("Receita e orçamento informados", f"""
SELECT g.nome_genero,
       ROUND(AVG(f.lucro_brl), 2) AS lucro_medio_brl,
       ROUND(AVG(f.lucro_usd), 2) AS lucro_medio_usd,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE {FINANCEIRO_COMPLETO}
GROUP BY g.nome_genero
ORDER BY lucro_medio_brl DESC"""),
        [Consulta("Literal: só receita informada", """
SELECT g.nome_genero,
       ROUND(AVG(f.lucro_brl), 2) AS lucro_medio_brl,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_brl > 0
GROUP BY g.nome_genero
ORDER BY lucro_medio_brl DESC""")],
    ),
    Pergunta(
        "Q03", "Bilheteria e Finanças",
        "Filmes com maior margem de lucro, entre os que possuem receita e orçamento informados",
        [
            "Margem = (receita - orçamento) / receita. A ordem é a mesma do ROI "
            "(lucro / orçamento): as duas crescem com receita/orçamento. Muda só o número.",
            "O topo é dominado por orçamentos irrisórios (US$ 1, US$ 128...): erros da "
            "origem. 87 filmes com ambos informados têm algum valor < US$ 1.000. O agente "
            "deve sinalizar isso; a variante usa orçamento mínimo de US$ 100 mil.",
        ],
        Consulta("Margem sobre a receita", f"""
SELECT m.titulo, f.orcamento_usd, f.receita_usd,
       ROUND(100.0 * (f.receita_usd - f.orcamento_usd) / f.receita_usd, 2) AS margem_pct
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE {FINANCEIRO_COMPLETO}
ORDER BY (f.receita_usd - f.orcamento_usd) * 1.0 / f.receita_usd DESC
LIMIT 10"""),
        [Consulta("Com orçamento >= US$ 100 mil", """
SELECT m.titulo, f.orcamento_usd, f.receita_usd,
       ROUND(100.0 * (f.receita_usd - f.orcamento_usd) / f.receita_usd, 2) AS margem_pct
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.receita_usd > 0 AND f.orcamento_usd >= 100000
ORDER BY (f.receita_usd - f.orcamento_usd) * 1.0 / f.receita_usd DESC
LIMIT 10""")],
    ),
    # ------------------------------------------------------------------ Popularidade
    Pergunta(
        "Q04", "Popularidade e Engajamento", "Os 5 filmes mais populares",
        [
            "3 dos 5 primeiros na consulta ingênua têm popularidade 2020.0, 2019.0 e "
            "2018.0: é o ano vazado de outra coluna. A principal descarta esses valores.",
        ],
        Consulta("Sem anos vazados", f"""
SELECT m.titulo, m.ano_lancamento, f.popularidade
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.popularidade IS NOT NULL AND {POPULARIDADE_VALIDA}
ORDER BY f.popularidade DESC
LIMIT 5"""),
        [Consulta("Ingênua (o que um LLM tende a gerar)", """
SELECT m.titulo, f.popularidade
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.popularidade IS NOT NULL
ORDER BY f.popularidade DESC
LIMIT 5""")],
    ),
    Pergunta(
        "Q05", "Popularidade e Engajamento",
        "Filmes com maior divergência entre a nota TMDB e a nota IMDb",
        [
            "nota_tmdb = 0 significa 'sem votos' (36 mil filmes); exige qtd_tmdb > 0 e "
            "nota_imdb > 0. As duas notas estão na escala 0-10. qtd_imdb pode ser nula "
            "mesmo com nota_imdb preenchida.",
            "Sem mínimo de votos, o topo é de filmes com 1 voto. A variante exige >= 100 "
            "votos em cada base.",
        ],
        Consulta("Ambas as notas com votos", """
SELECT m.titulo, f.nota_tmdb, f.qtd_tmdb, f.nota_imdb, f.qtd_imdb,
       ROUND(ABS(f.nota_tmdb - f.nota_imdb), 2) AS divergencia
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.qtd_tmdb > 0 AND f.nota_tmdb > 0 AND f.nota_imdb > 0
ORDER BY divergencia DESC, f.qtd_imdb DESC
LIMIT 10"""),
        [Consulta("Com >= 100 votos em cada base", """
SELECT m.titulo, f.nota_tmdb, f.qtd_tmdb, f.nota_imdb, f.qtd_imdb,
       ROUND(ABS(f.nota_tmdb - f.nota_imdb), 2) AS divergencia
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.qtd_tmdb >= 100 AND f.qtd_imdb >= 100 AND f.nota_tmdb > 0
  AND f.nota_imdb > 0
ORDER BY divergencia DESC
LIMIT 10""")],
    ),
    Pergunta(
        "Q06", "Popularidade e Engajamento", "Nota média IMDb por ano de lançamento",
        [
            "Ignora nota_imdb nula ou 0. Anos >= 2025 têm poucos filmes: mostre a contagem "
            "para o usuário não confiar em médias de 1 filme.",
        ],
        Consulta("Por ano", """
SELECT m.ano_lancamento,
       ROUND(AVG(f.nota_imdb), 2) AS nota_media_imdb,
       COUNT(*) AS filmes_com_nota
FROM fact_movies_performance f
JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.nota_imdb > 0 AND m.ano_lancamento IS NOT NULL
GROUP BY m.ano_lancamento
ORDER BY m.ano_lancamento"""),
    ),
    # ------------------------------------------------------------------ Elenco e equipe
    Pergunta(
        "Q07", "Elenco e Equipe",
        "Ator com mais participações em filmes lançados nos últimos 5 anos",
        [
            f"Janela relativa à data atual ({DATA_HOJE}): lançamento entre hoje - 5 anos e "
            "hoje, status 'Lançado'. A variante usa a última data de lançamento da base "
            "(2026-02-19), a regra do seu projeto Databricks. Escolha uma e documente.",
            "O mesmo filme aparece com vários ids (221 obras, 410 ids excedentes). A "
            "principal conta por obra (título normalizado + data), como no seu projeto.",
        ],
        Consulta("Janela até hoje, contando por obra", f"""
WITH filmes AS (
    SELECT sk_movie_id, LOWER(TRIM(titulo)) || '|' || data_lancamento AS obra
    FROM dim_movies
    WHERE status_filme = 'Lançado'
      AND data_lancamento BETWEEN DATE('{DATA_HOJE}', '-5 years') AND DATE('{DATA_HOJE}')
)
SELECT p.nome_pessoa AS ator, COUNT(DISTINCT fi.obra) AS filmes
FROM filmes fi
JOIN bridge_movie_person b ON b.sk_movie_id = fi.sk_movie_id
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
WHERE p.tipo_pessoa = 'Ator' AND p.nome_pessoa NOT IN {NOMES_INVALIDOS}
GROUP BY p.sk_person_id
ORDER BY filmes DESC, ator
LIMIT 10"""),
        [
            Consulta("Contando por id (ingênua)", f"""
SELECT p.nome_pessoa AS ator, COUNT(*) AS filmes
FROM dim_movies m
JOIN bridge_movie_person b ON b.sk_movie_id = m.sk_movie_id
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
WHERE p.tipo_pessoa = 'Ator' AND m.status_filme = 'Lançado'
  AND m.data_lancamento BETWEEN DATE('{DATA_HOJE}', '-5 years') AND DATE('{DATA_HOJE}')
GROUP BY p.sk_person_id
ORDER BY filmes DESC, ator
LIMIT 10"""),
            Consulta("Janela até a última data de lançamento da base, por obra", f"""
WITH ref AS (
    SELECT MAX(data_lancamento) AS d FROM dim_movies
    WHERE status_filme = 'Lançado' AND data_lancamento <= DATE('{DATA_HOJE}')
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
WHERE p.tipo_pessoa = 'Ator' AND p.nome_pessoa NOT IN {NOMES_INVALIDOS}
GROUP BY p.sk_person_id
ORDER BY filmes DESC, ator
LIMIT 10"""),
        ],
    ),
    Pergunta(
        "Q08", "Elenco e Equipe", "Diretores com maior nota média (mínimo de 5 filmes)",
        [
            "Nota = IMDb (a mais completa). O mínimo de 5 conta só filmes com nota, senão "
            "a média sairia de menos de 5 filmes.",
            "Exclui nomes inválidos ('English', 'Documentary'...), que aqui não chegam ao "
            "topo, mas aparecem em outras perguntas sobre pessoas.",
        ],
        Consulta("Nota IMDb, >= 5 filmes com nota", f"""
SELECT p.nome_pessoa AS diretor,
       ROUND(AVG(f.nota_imdb), 2) AS nota_media_imdb,
       COUNT(*) AS filmes
FROM bridge_movie_person b
JOIN dim_people p ON p.sk_person_id = b.sk_person_id
JOIN fact_movies_performance f ON f.sk_movie_id = b.sk_movie_id
WHERE p.tipo_pessoa = 'Diretor' AND f.nota_imdb > 0
  AND p.nome_pessoa NOT IN {NOMES_INVALIDOS}
GROUP BY p.sk_person_id
HAVING COUNT(*) >= 5
ORDER BY nota_media_imdb DESC, filmes DESC
LIMIT 10"""),
    ),
    Pergunta(
        "Q09", "Elenco e Equipe", "Dupla ator–diretor que mais trabalhou junta",
        [
            "Não há coluna de papel na bridge: o papel vem de dim_people.tipo_pessoa, então "
            "a bridge_movie_person entra duas vezes (uma para o ator, outra para o diretor).",
            "Conta por obra: as duplas de 'Chad Payne' (31) vêm de filmes repetidos com "
            "vários ids. Sem deduplicar, o ranking muda.",
            "Exclui a mesma pessoa como ator e diretor de si mesma (nomes iguais).",
        ],
        Consulta("Por obra", f"""
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
  AND a.nome_pessoa NOT IN {NOMES_INVALIDOS}
  AND d.nome_pessoa NOT IN {NOMES_INVALIDOS}
GROUP BY a.sk_person_id, d.sk_person_id
ORDER BY filmes_juntos DESC, ator
LIMIT 10"""),
        [Consulta("Por id (ingênua)", """
SELECT a.nome_pessoa AS ator, d.nome_pessoa AS diretor, COUNT(*) AS filmes_juntos
FROM bridge_movie_person ba
JOIN dim_people a ON a.sk_person_id = ba.sk_person_id AND a.tipo_pessoa = 'Ator'
JOIN bridge_movie_person bd ON bd.sk_movie_id = ba.sk_movie_id
JOIN dim_people d ON d.sk_person_id = bd.sk_person_id AND d.tipo_pessoa = 'Diretor'
GROUP BY a.sk_person_id, d.sk_person_id
ORDER BY filmes_juntos DESC, ator
LIMIT 10""")],
    ),
    # ------------------------------------------------------------------ Gêneros e produtoras
    Pergunta(
        "Q10", "Gêneros e Produtoras", "Quantidade de filmes por gênero",
        [
            "Catálogo inteiro (todos os status). Um filme com 3 gêneros conta em cada um, "
            "então a soma passa do total de filmes.",
        ],
        Consulta("Por gênero", """
SELECT g.nome_genero, COUNT(DISTINCT bg.sk_movie_id) AS filmes
FROM bridge_movie_genre bg
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
GROUP BY g.nome_genero
ORDER BY filmes DESC, g.nome_genero"""),
    ),
    Pergunta(
        "Q11", "Gêneros e Produtoras", "Produtora com maior lucro total",
        [
            "Soma lucro_brl só de filmes com receita E orçamento > 0. Somar lucro_brl de "
            "todos inclui receitas sem custo como lucro e muda a 3ª e a 4ª posição.",
            "Coprodução: o lucro integral do filme conta para cada produtora.",
        ],
        Consulta("Receita e orçamento informados", f"""
SELECT c.nome_produtora,
       ROUND(SUM(f.lucro_brl), 2) AS lucro_total_brl,
       ROUND(SUM(f.lucro_usd), 2) AS lucro_total_usd,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_company bc ON bc.sk_movie_id = f.sk_movie_id
JOIN dim_companies c ON c.sk_company_id = bc.sk_company_id
WHERE {FINANCEIRO_COMPLETO} AND c.nome_produtora NOT IN {NOMES_INVALIDOS}
GROUP BY c.sk_company_id
ORDER BY lucro_total_brl DESC
LIMIT 10"""),
        [Consulta("Ingênua: SUM(lucro_brl) sem filtro", """
SELECT c.nome_produtora, ROUND(SUM(f.lucro_brl), 2) AS lucro_total_brl
FROM fact_movies_performance f
JOIN bridge_movie_company bc ON bc.sk_movie_id = f.sk_movie_id
JOIN dim_companies c ON c.sk_company_id = bc.sk_company_id
GROUP BY c.sk_company_id
ORDER BY lucro_total_brl DESC
LIMIT 10""")],
    ),
    Pergunta(
        "Q12", "Gêneros e Produtoras", "Gênero com maior margem de lucro média",
        [
            "Principal: margem do gênero = SUM(lucro) / SUM(receita), com receita e "
            "orçamento > 0. É a média ponderada pela receita e não é distorcida por "
            "valores extremos.",
            "A média simples das margens por filme não serve: (receita - orçamento) / "
            "receita não tem piso, e um fracasso com receita de US$ 1 vale -1.000.000%. "
            "Todos os gêneros ficam negativos. A média do ROI (lucro / orçamento) explode "
            "no sentido oposto, com orçamentos de US$ 1. As duas variantes mostram isso.",
            "Explique a escolha no README: a pergunta é ambígua e o resultado depende "
            "da fórmula.",
        ],
        Consulta("Margem agregada: SUM(lucro) / SUM(receita)", f"""
SELECT g.nome_genero,
       ROUND(100.0 * SUM(f.receita_usd - f.orcamento_usd) / SUM(f.receita_usd), 2)
           AS margem_pct,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE {FINANCEIRO_COMPLETO}
GROUP BY g.nome_genero
ORDER BY margem_pct DESC"""),
        [
            Consulta("Média simples da margem por filme", f"""
SELECT g.nome_genero,
       ROUND(100.0 * AVG((f.receita_usd - f.orcamento_usd) * 1.0 / f.receita_usd), 2)
           AS margem_media_pct,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE {FINANCEIRO_COMPLETO}
GROUP BY g.nome_genero
ORDER BY margem_media_pct DESC"""),
            Consulta("ROI médio (lucro / orçamento)", f"""
SELECT g.nome_genero,
       ROUND(100.0 * AVG((f.receita_usd - f.orcamento_usd) * 1.0 / f.orcamento_usd), 2)
           AS roi_medio_pct,
       COUNT(*) AS filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE {FINANCEIRO_COMPLETO}
GROUP BY g.nome_genero
ORDER BY roi_medio_pct DESC"""),
        ],
    ),
    # ------------------------------------------------------------------ Avaliações
    Pergunta(
        "Q13", "Avaliações dos Usuários", "Filmes mais avaliados pelos usuários",
        [
            "Conta em movie_reviews. Neste banco a dim_reviews bate com movie_reviews (só "
            "5 médias diferem no arredondamento), então qtd_avaliacoes_usuarios também serve.",
            "Por id, o top 10 é todo de cópias de 'Die Hart' (o mesmo filme com até 25 "
            "ids). A principal soma as avaliações por obra (título + data).",
            "Limitação: algumas cópias têm datas diferentes ('Emesis Blue' em 17, 20 e 21/02/2023), "
            "então a chave título + data não junta tudo. Agrupar só pelo título juntaria "
            "filmes diferentes com o mesmo nome. Vale citar no README.",
        ],
        Consulta("Por obra", """
SELECT MIN(m.titulo) AS titulo, m.data_lancamento,
       COUNT(*) AS avaliacoes, ROUND(AVG(r.rating), 2) AS nota_media_usuarios,
       COUNT(DISTINCT m.sk_movie_id) AS ids_no_catalogo
FROM movie_reviews r
JOIN dim_movies m ON m.sk_movie_id = r.sk_movie_id
GROUP BY LOWER(TRIM(m.titulo)), m.data_lancamento
ORDER BY avaliacoes DESC, titulo
LIMIT 10"""),
        [Consulta("Por id (ingênua)", """
SELECT m.titulo, COUNT(*) AS avaliacoes, ROUND(AVG(r.rating), 2) AS nota_media_usuarios
FROM movie_reviews r
JOIN dim_movies m ON m.sk_movie_id = r.sk_movie_id
GROUP BY r.sk_movie_id
ORDER BY avaliacoes DESC, m.titulo
LIMIT 10""")],
    ),
    Pergunta(
        "Q14", "Avaliações dos Usuários",
        "Filmes em que a nota média dos usuários mais diverge da nota IMDb",
        [
            "Média dos usuários calculada de movie_reviews.rating (escala 0-10, como o "
            "IMDb). Exige nota_imdb > 0.",
            "Com 1 avaliação, a 'média' é uma única nota aleatória. A variante exige >= 3 "
            "avaliações.",
        ],
        Consulta("Todos os filmes avaliados", """
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
LIMIT 10"""),
        [Consulta("Com >= 3 avaliações", """
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
LIMIT 10""")],
    ),
]


def executar(conn: sqlite3.Connection, sql: str) -> dict:
    cur = conn.execute(sql)
    colunas = [d[0] for d in cur.description]
    return {"colunas": colunas, "linhas": [list(r) for r in cur.fetchall()]}


def tabela_md(resultado: dict, max_linhas: int = 20) -> str:
    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, int) and abs(v) >= 10_000:
            return f"{v:,}"
        return "—" if v is None else str(v).replace("|", "\\|")

    cab = "| " + " | ".join(resultado["colunas"]) + " |"
    sep = "|" + "---|" * len(resultado["colunas"])
    corpo = ["| " + " | ".join(fmt(v) for v in r) + " |"
             for r in resultado["linhas"][:max_linhas]]
    extra = len(resultado["linhas"]) - max_linhas
    if extra > 0:
        corpo.append(f"| … mais {extra} linhas |" + " |" * (len(resultado["colunas"]) - 1))
    return "\n".join([cab, sep, *corpo])


def main(caminho_db: str) -> None:
    conn = sqlite3.connect(f"file:{Path(caminho_db).as_posix()}?mode=ro", uri=True)
    saida, md = [], [
        "# Gabarito: SQL de referência das perguntas do enunciado",
        "",
        f"Gerado por `gabarito.py` sobre o `cinerocket.db`. Data de referência: {DATA_HOJE}. "
        "Valores em R$ usam as colunas `_brl` do banco (cotação histórica por filme).",
    ]
    for p in PERGUNTAS:
        item = asdict(p)
        item["principal"]["resultado"] = executar(conn, p.principal.sql)
        for v, cv in zip(item["variantes"], p.variantes):
            v["resultado"] = executar(conn, cv.sql)
        saida.append(item)

        md += ["", f"## {p.id}. {p.pergunta}", f"*{p.categoria}*", "", "**Decisões**", ""]
        md += [f"- {d}" for d in p.decisoes]
        blocos = [(f"Resposta esperada: {p.principal.titulo}", item["principal"])]
        blocos += [(f"Variante: {v['titulo']}", v) for v in item["variantes"]]
        for titulo, b in blocos:
            md += ["", f"**{titulo}**", "", "```sql", b["sql"].strip(), "```", "",
                   tabela_md(b["resultado"])]

    Path("gabarito.json").write_text(
        json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    Path("GABARITO.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"OK: {len(saida)} perguntas -> gabarito.json e GABARITO.md")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cinerocket.db")

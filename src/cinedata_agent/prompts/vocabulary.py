"""Domain vocabulary rendered into the system prompt."""

# dim_genres.nome_genero (English, as in TMDB) -> Portuguese, so the model can map user words.
GENRES: dict[str, str] = {
    "Action": "ação",
    "Adventure": "aventura",
    "Animation": "animação",
    "Comedy": "comédia",
    "Crime": "crime",
    "Documentary": "documentário",
    "Drama": "drama",
    "Family": "família",
    "Fantasy": "fantasia",
    "History": "história",
    "Horror": "terror",
    "Music": "música",
    "Mystery": "mistério",
    "Romance": "romance",
    "Science Fiction": "ficção científica",
    "Thriller": "suspense",
    "Tv Movie": "filme para TV",
    "War": "guerra",
    "Western": "faroeste",
}

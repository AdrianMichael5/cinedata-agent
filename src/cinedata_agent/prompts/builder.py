"""Render the system prompt for the configured database backend."""

import re
from dataclasses import dataclass
from importlib import resources

from cinedata_agent.config import Settings
from cinedata_agent.prompts.vocabulary import GENRES

# The system prompt is resent on every model call, so it must stay small.
MAX_PROMPT_CHARS = 24_000

TEMPLATES: dict[str, str] = {"sqlite": "sqlite.md"}

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
_EXAMPLE = re.compile(r"^Pergunta: (?P<question>.+?)\n```sql\n(?P<sql>.+?)\n```", re.M | re.S)


@dataclass(frozen=True)
class PromptExample:
    question: str
    sql: str


def build_system_prompt(settings: Settings) -> str:
    """Fill the backend template with the reference date, limits and genres.

    Invalid names stay as the {{NOMES_INVALIDOS}} marker; run_query expands it.
    """
    template_name = TEMPLATES.get(settings.db_backend)
    if template_name is None:
        raise NotImplementedError(
            f"O prompt do backend {settings.db_backend} é um extra opcional e ainda não existe."
        )
    template = resources.files(__package__).joinpath(template_name).read_text(encoding="utf-8")
    return render_template(
        template,
        {
            "reference_date": settings.reference_date.isoformat(),
            "max_rows": str(settings.max_rows),
            "query_timeout_seconds": str(settings.query_timeout_seconds),
            "genres": _genres_text(),
        },
    )


def render_template(template: str, values: dict[str, str]) -> str:
    """Replace {name} placeholders; fail if any placeholder is left unfilled.

    str.replace, not str.format: SQL and JSON in the template contain their own braces.
    """
    rendered = template
    for name, value in values.items():
        rendered = rendered.replace(f"{{{name}}}", value)

    leftover = _PLACEHOLDER.findall(rendered)
    if leftover:
        raise ValueError(f"Unfilled template placeholders: {sorted(set(leftover))}")
    return rendered


def prompt_examples(prompt: str) -> list[PromptExample]:
    """Question/SQL pairs written as 'Pergunta: ...' followed by a ```sql block."""
    return [
        PromptExample(question=match["question"].strip(), sql=match["sql"].strip())
        for match in _EXAMPLE.finditer(prompt)
    ]


def _genres_text() -> str:
    return ", ".join(f"{english} ({portuguese})" for english, portuguese in GENRES.items())

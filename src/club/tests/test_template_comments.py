"""Django's {# #} comment is single-line: one that wraps onto a second line is not a comment at
all and is rendered as text. Multi-line comments use {% comment %}...{% endcomment %}."""

from django.conf import settings


def unclosed_comments() -> list[str]:
    """Every line of a template under src/templates with a "{#" and no "#}" after it."""
    root = settings.SRC_DIR / "templates"
    found = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            start = line.find("{#")
            while start != -1:
                end = line.find("#}", start + 2)
                if end == -1:
                    found.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
                    break
                start = line.find("{#", end + 2)
    return found


def test_every_comment_closes_on_its_line():
    assert unclosed_comments() == []


def test_the_check_finds_a_wrapped_comment(settings, tmp_path):
    (tmp_path / "templates" / "live").mkdir(parents=True)
    (tmp_path / "templates" / "live" / "x.html").write_text(
        "{# fine #}\n<p>{# also fine #} {# wraps\n   on #}</p>\n"
    )
    settings.SRC_DIR = tmp_path
    assert unclosed_comments() == ["live/x.html:2: <p>{# also fine #} {# wraps"]

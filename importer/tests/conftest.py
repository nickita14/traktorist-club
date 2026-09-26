from io import StringIO

import openpyxl
import pytest
import yaml
from django.core.management import call_command

from importer.tests.sheet_builder import build_workbook, fixture_aliases


@pytest.fixture
def workbook_path(tmp_path):
    return build_workbook(tmp_path / "club.xlsx")


@pytest.fixture
def write_aliases(tmp_path):
    def write(data=None, name="aliases.yaml"):
        path = tmp_path / name
        path.write_text(
            yaml.safe_dump(fixture_aliases() if data is None else data, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    return write


@pytest.fixture
def aliases_path(write_aliases):
    return write_aliases()


@pytest.fixture
def run_command():
    def run(name, *args, **options):
        out = StringIO()
        call_command(name, *[str(a) for a in args], stdout=out, **options)
        return out.getvalue()

    return run


def edit_cells(path, title, values: dict):
    """Overwrite single cells of a built workbook, for layouts the builder does not model."""
    workbook = openpyxl.load_workbook(path)
    for cell, value in values.items():
        workbook[title][cell] = value
    workbook.save(path)
    return path

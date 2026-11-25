import pytest

from tests.fakes import BancoFake
from tests.unit.apoio_http import Saude


@pytest.fixture
def banco() -> BancoFake:
    return BancoFake()


@pytest.fixture
def saude() -> Saude:
    return Saude()

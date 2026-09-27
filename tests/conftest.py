import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache_between_tests():
    """
    Clears cache between tests so DRF rate throttles do not leak across test cases.
    """
    cache.clear()
    yield
    cache.clear()

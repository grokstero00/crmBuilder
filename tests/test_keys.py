from crmbuilder.keys import KeyAllocator, is_meaningful_code, slugify


def test_slugify_transliterates_cyrillic():
    assert slugify("Первичный приём") == "pervichnyy_priem"
    assert slugify("Щука & Ёж") == "shchuka_ezh"


def test_slugify_prefixes_leading_digit():
    assert slugify("1С интеграция") == "n1s_integratsiya"


def test_auto_generated_codes_are_not_meaningful():
    assert not is_meaningful_code("1712345678")
    assert not is_meaningful_code("UC_X7K2P1")
    assert not is_meaningful_code("0c8c2f1b3e5a4d6f8a9b0c1d2e3f4a5b")
    assert is_meaningful_code("DOCTOR")


def test_allocator_deduplicates_and_falls_back():
    keys = KeyAllocator()
    assert keys.allocate("Врач", fallback="x") == "vrach"
    assert keys.allocate("Врач", fallback="x") == "vrach_2"
    assert keys.allocate("", "!!!", fallback="field_9") == "field_9"

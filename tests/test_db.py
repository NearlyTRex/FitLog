import pytest


def test_unknown_setting_is_rejected(db):
    with pytest.raises(KeyError):
        db.set_setting("not_a_setting", "1")
    assert db.get_setting("calorie_target") == "2000"

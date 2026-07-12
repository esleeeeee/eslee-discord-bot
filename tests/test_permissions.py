from eslee_bot.utils.permissions import has_management_permission


def test_guild_owner_is_allowed() -> None:
    assert has_management_permission(user_id=10, guild_owner_id=10, administrator=False)


def test_administrator_is_allowed() -> None:
    assert has_management_permission(user_id=20, guild_owner_id=10, administrator=True)


def test_regular_member_is_rejected() -> None:
    assert not has_management_permission(user_id=20, guild_owner_id=10, administrator=False)

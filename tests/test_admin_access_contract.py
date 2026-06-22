from __future__ import annotations


def test_denied_permission_rows_do_not_grant_staff_access(monkeypatch):
    import services.admin as admin

    monkeypatch.setattr(admin, "ADMIN_IDS", [], raising=False)
    monkeypatch.setattr(admin, "_roles_for", lambda user_id: set())
    monkeypatch.setattr(admin, "_allowed_permissions_for", lambda user_id: set())

    assert admin.has_any_allowed_permission(1001) is False
    assert admin.is_staff(1001) is False
    assert admin.is_admin(1001) is False
    assert admin.is_platform_admin(1001) is False


def test_allowed_permission_without_role_does_not_create_staff_identity(monkeypatch):
    import services.admin as admin

    monkeypatch.setattr(admin, "ADMIN_IDS", [], raising=False)
    monkeypatch.setattr(admin, "_roles_for", lambda user_id: set())
    monkeypatch.setattr(admin, "_allowed_permissions_for", lambda user_id: {"admin:users:today"})

    assert admin.has_any_allowed_permission(1001) is True
    assert admin.is_staff(1001) is False
    assert admin.is_admin(1001) is False
    assert admin.is_platform_admin(1001) is False
    assert admin.can_use_scoped_admin_permission(1001, "admin:users:today") is False


def test_staff_role_with_allowed_permission_can_use_scoped_permission(monkeypatch):
    import services.admin as admin

    monkeypatch.setattr(admin, "ADMIN_IDS", [], raising=False)
    monkeypatch.setattr(admin, "_roles_for", lambda user_id: {"support"})
    monkeypatch.setattr(admin, "_allowed_permissions_for", lambda user_id: {"admin:users:today"})

    assert admin.is_staff(1001) is True
    assert admin.is_platform_admin(1001) is False
    assert admin.can_use_scoped_admin_permission(1001, "admin:users:today") is True
    assert admin.can_use_scoped_admin_permission(1001, "admin:money:today") is False


def test_admin_role_grants_platform_admin(monkeypatch):
    import services.admin as admin

    monkeypatch.setattr(admin, "ADMIN_IDS", [], raising=False)
    monkeypatch.setattr(admin, "_roles_for", lambda user_id: {"admin"})
    monkeypatch.setattr(admin, "_allowed_permissions_for", lambda user_id: None)

    assert admin.is_staff(1001) is True
    assert admin.is_platform_admin(1001) is True


def test_superadmin_grants_staff_and_platform_admin(monkeypatch):
    import services.admin as admin

    monkeypatch.setattr(admin, "ADMIN_IDS", [1001], raising=False)
    monkeypatch.setattr(admin, "_roles_for", lambda user_id: set())
    monkeypatch.setattr(admin, "_allowed_permissions_for", lambda user_id: set())

    assert admin.is_staff(1001) is True
    assert admin.is_admin(1001) is True
    assert admin.is_platform_admin(1001) is True
    assert admin.can_use_scoped_admin_permission(1001, "anything") is True

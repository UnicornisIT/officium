"""Who may open the personal finance pages (everything outside /admin)."""

from app.models import User


def can_use_personal_cabinet(user):
    """Ordinary users always; administrators only as real accounts from the users table.

    The emergency administrator (``AdminUser``, id ``'admin'``) owns no data, so
    it stays inside the admin panel.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'is_admin', False):
        return True
    return isinstance(user, User)

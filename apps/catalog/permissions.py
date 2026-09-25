from rest_framework import permissions


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Custom permission:
    - Any authenticated user can perform read-only requests (GET, HEAD, OPTIONS).
    - Write requests (POST, PUT, PATCH, DELETE) require admin/staff credentials.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        if request.method in permissions.SAFE_METHODS:
            return True

        return bool(request.user.is_staff)

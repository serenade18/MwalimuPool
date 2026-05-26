from rest_framework.permissions import BasePermission
from mwalimuApp.models import UserAccount


class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.user_type == UserAccount.UserTypes.ADMIN
        )


class IsSchoolRole(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.user_type == UserAccount.UserTypes.SCHOOL
        )


class IsTeacherRole(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.user_type == UserAccount.UserTypes.TEACHER
        )


class IsSchoolOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.user_type in (
                UserAccount.UserTypes.SCHOOL,
                UserAccount.UserTypes.ADMIN,
            )
        )


class IsTeacherOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.user_type in (
                UserAccount.UserTypes.TEACHER,
                UserAccount.UserTypes.ADMIN,
            )
        )


def any_of(*permission_classes):
    """Returns a permission class that passes if any of the given classes pass."""
    class AnyOf(BasePermission):
        def has_permission(self, request, view):
            return any(p().has_permission(request, view) for p in permission_classes)
    return AnyOf
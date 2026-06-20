"""
"""
from django.contrib import admin
from django.conf.urls.static import static
from django.urls import path, include, re_path
from django.views.generic import TemplateView
from django.views.static import serve
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from mwalimuApp.views import PaymentViewSet, TeacherProfileViewSet, SchoolProfileViewSet, JobPostingViewSet, \
    BookingViewSet, RatingViewSet, DisputeViewSet, UserViewSet
from mwalimuProject import settings

router = DefaultRouter()
router.register(r"teachers", TeacherProfileViewSet, basename="teachers")
router.register(r"schools", SchoolProfileViewSet, basename="schools")
router.register(r"jobs", JobPostingViewSet, basename="jobs")
router.register(r"bookings", BookingViewSet, basename="bookings")
router.register(r"payments", PaymentViewSet, basename="payments")
router.register(r"ratings", RatingViewSet, basename="ratings")
router.register(r"disputes", DisputeViewSet, basename="disputes")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path("api/users/", UserViewSet.as_view({"post": "create", "get": "list"})),
    path("api/users/<int:pk>/", UserViewSet.as_view({"get": "retrieve"})),
    path("api/users/<int:pk>/status/", UserViewSet.as_view({"patch": "update_status"})),
    path("api/userinfo/", UserViewSet.as_view({"get": "userinfo"})),
    path("api/gettoken/", UserViewSet.as_view({"post": "login"})),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/password-reset/", UserViewSet.as_view({"post": "forgot_password"})),
    path("api/password-reset/confirm/", UserViewSet.as_view({"post": "reset_password"})),
    path("api/activate/<str:uidb64>/<str:token>/",
         UserViewSet.as_view({"get": "activate", "post": "activate"})),
]

if settings.DEBUG:
    urlpatterns += static(
        '/assets/',
        document_root=settings.REACT_BUILD_DIR / 'assets'
    )
    urlpatterns += [
        re_path(r'^favicon\.png$', serve, {
            'document_root': settings.REACT_BUILD_DIR,
            'path': 'favicon.png'
        }),
    ]

# Serve React's index.html for all other routes
urlpatterns += [
    re_path(r'^(?!assets/|static/|media/).*$', TemplateView.as_view(template_name='index.html')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


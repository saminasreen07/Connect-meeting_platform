from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('meetings/', include('meetings.urls')),
    path('', TemplateView.as_view(template_name='landing/index.html'), name='landing'),
] + static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
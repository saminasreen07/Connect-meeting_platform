from django.urls import path
from . import views

urlpatterns = [
    path('create/', views.create_meeting, name='create_meeting'),
    path('created/<str:room_code>/', views.meeting_created, name='meeting_created'),
    path('join/', views.join_meeting, name='join_meeting'),
    path('room/<str:room_code>/', views.meeting_room, name='meeting_room'),
    path('end/<str:room_code>/', views.end_meeting, name='end_meeting'),
    path('leave/<str:room_code>/', views.leave_meeting, name='leave_meeting'),
    path('history/', views.meeting_history, name='meeting_history'),
    path('history/<str:room_code>/', views.meeting_details, name='meeting_details'),
    path('delete/<str:room_code>/', views.delete_meeting, name='delete_meeting'),
    path('translate/', views.translate_text, name='translate_text'),
    path('history/<str:room_code>/insights/', views.generate_insights, name='generate_insights'),
]
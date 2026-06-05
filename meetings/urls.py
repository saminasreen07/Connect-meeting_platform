from django.urls import path
from . import views

urlpatterns = [
    path('create/', views.create_meeting, name='create_meeting'),
    path('join/', views.join_meeting, name='join_meeting'),
    path('room/<str:room_code>/', views.meeting_room, name='meeting_room'),
    path('end/<str:room_code>/', views.end_meeting, name='end_meeting'),
    path('leave/<str:room_code>/', views.leave_meeting, name='leave_meeting'),
]
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from .models import Meeting, Participant

@login_required
def create_meeting(request):
    if request.method == 'POST':
        title = request.POST.get('title', 'Untitled Meeting')
        meeting = Meeting.objects.create(
            host=request.user,
            title=title,
            started_at=timezone.now()
        )
        Participant.objects.create(meeting=meeting, user=request.user)
        return redirect('meeting_room', room_code=meeting.room_code)
    return render(request, 'meetings/create_meeting.html')

@login_required
def join_meeting(request):
    if request.method == 'POST':
        room_code = request.POST.get('room_code', '').strip().upper()
        meeting = get_object_or_404(Meeting, room_code=room_code, is_active=True)
        Participant.objects.get_or_create(meeting=meeting, user=request.user)
        return redirect('meeting_room', room_code=room_code)
    return render(request, 'meetings/join_meeting.html')

@login_required
def meeting_room(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code, is_active=True)
    participants = meeting.participants.filter(is_active=True).select_related('user')
    return render(request, 'meetings/room.html', {
        'meeting': meeting,
        'participants': participants,
        'room_code': room_code,
        'user': request.user,
        'is_host': meeting.host == request.user,
    })

@login_required
def end_meeting(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code, host=request.user)
    meeting.is_active = False
    meeting.ended_at = timezone.now()
    meeting.save()
    meeting.participants.filter(is_active=True).update(is_active=False, left_at=timezone.now())
    return redirect('dashboard')

@login_required
def leave_meeting(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code)
    Participant.objects.filter(meeting=meeting, user=request.user).update(
        is_active=False, left_at=timezone.now()
    )
    return redirect('dashboard')
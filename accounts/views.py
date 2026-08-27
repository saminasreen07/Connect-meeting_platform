from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from .forms import SignupForm, LoginForm

def signup_view(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('dashboard')
    else:
        form = SignupForm()
    return render(request, 'accounts/signup.html', {'form': form})

from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone

def login_view(request):
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('dashboard')
    else:
        form = LoginForm()
    return render(request, 'accounts/login.html', {'form': form})

def logout_view(request):
    logout(request)
    return redirect('login')

from meetings.models import Meeting

@login_required
def dashboard_view(request):
    now = timezone.now()
    recent_meetings = Meeting.objects.filter(participants__user=request.user).distinct().order_by('-created_at')[:5]
    total_meetings = Meeting.objects.filter(participants__user=request.user).distinct().count()
    meetings_this_month = Meeting.objects.filter(
        participants__user=request.user,
        created_at__year=now.year,
        created_at__month=now.month
    ).distinct().count()
    return render(request, 'accounts/dashboard.html', {
        'user': request.user,
        'recent_meetings': recent_meetings,
        'total_meetings': total_meetings,
        'meetings_this_month': meetings_this_month,
    })
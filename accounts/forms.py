from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .models import User

class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(choices=[('host', 'Host'), ('participant', 'Participant')])

    class Meta:
        model = User
        fields = ['username', 'email', 'role', 'password1', 'password2']

class LoginForm(AuthenticationForm):
    pass
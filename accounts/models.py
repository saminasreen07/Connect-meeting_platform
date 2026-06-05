from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = (
        ('host', 'Host'),
        ('participant', 'Participant'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='participant')
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)

    def __str__(self):
        return self.username
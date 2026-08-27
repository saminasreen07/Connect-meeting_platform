from django.db import models
from meetings.models import Meeting

class AIInsight(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name='ai_insight')
    summary = models.TextField(blank=True, null=True)
    action_items = models.TextField(blank=True, null=True)
    speaker_stats = models.JSONField(blank=True, null=True)  # Stores dictionary of speaking time percentages
    sentiment_score = models.CharField(max_length=50, blank=True, null=True)
    created_at_language = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AI Insights for meeting {self.meeting.room_code}"

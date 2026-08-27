from django.contrib import admin
from .models import AIInsight

@admin.register(AIInsight)
class AIInsightAdmin(admin.ModelAdmin):
    list_display = ('meeting', 'sentiment_score', 'created_at')
    list_filter = ('sentiment_score', 'created_at')
    search_fields = ('meeting__room_code', 'summary', 'action_items')

from django.contrib import admin
from .models import Meeting, Participant, TranscriptMessage

@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ('title', 'room_code', 'host', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('title', 'room_code', 'host__username')

@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ('user', 'meeting', 'joined_at', 'left_at', 'is_active')
    list_filter = ('is_active', 'joined_at')
    search_fields = ('user__username', 'meeting__room_code')

@admin.register(TranscriptMessage)
class TranscriptMessageAdmin(admin.ModelAdmin):
    list_display = ('speaker', 'meeting', 'text_excerpt', 'timestamp')
    list_filter = ('timestamp',)
    search_fields = ('speaker__username', 'meeting__room_code', 'text')

    def text_excerpt(self, obj):
        return obj.text[:50]
    text_excerpt.short_description = 'Text'

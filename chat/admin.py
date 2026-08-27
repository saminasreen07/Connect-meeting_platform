from django.contrib import admin
from .models import ChatMessage

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'meeting', 'message_excerpt', 'timestamp')
    list_filter = ('timestamp',)
    search_fields = ('sender__username', 'meeting__room_code', 'message')

    def message_excerpt(self, obj):
        return obj.message[:50]
    message_excerpt.short_description = 'Message'

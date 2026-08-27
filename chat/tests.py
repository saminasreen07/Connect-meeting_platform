from django.test import TestCase
from django.contrib.auth import get_user_model
from meetings.models import Meeting
from chat.models import ChatMessage

User = get_user_model()

class ChatMessageModelTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(username='hostuser', password='password123')
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.meeting = Meeting.objects.create(
            host=self.host,
            title='Test Meeting'
        )

    def test_chat_message_creation(self):
        message = ChatMessage.objects.create(
            meeting=self.meeting,
            sender=self.user,
            message='Hello this is a test message'
        )
        self.assertEqual(message.meeting, self.meeting)
        self.assertEqual(message.sender, self.user)
        self.assertEqual(message.message, 'Hello this is a test message')
        self.assertIsNotNone(message.timestamp)

    def test_chat_message_ordering(self):
        msg1 = ChatMessage.objects.create(
            meeting=self.meeting,
            sender=self.user,
            message='First message'
        )
        msg2 = ChatMessage.objects.create(
            meeting=self.meeting,
            sender=self.user,
            message='Second message'
        )
        messages = ChatMessage.objects.filter(meeting=self.meeting)
        self.assertEqual(list(messages), [msg1, msg2])

    def test_chat_message_str(self):
        message = ChatMessage.objects.create(
            meeting=self.meeting,
            sender=self.user,
            message='Short string'
        )
        self.assertEqual(str(message), 'testuser: Short string')

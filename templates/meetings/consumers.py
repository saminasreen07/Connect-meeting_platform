import json
from channels.generic.websocket import AsyncWebsocketConsumer

class MeetingConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        self.room_group_name = f'meeting_{self.room_code}'
        self.user = self.scope['user']

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_joined',
                'user': self.user.username,
                'channel': self.channel_name,
            }
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_left',
                'user': self.user.username,
                'channel': self.channel_name,
            }
        )
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        data = json.loads(text_data)
        data['sender_channel'] = self.channel_name
        data['sender'] = self.user.username

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'signal_message',
                'data': data,
            }
        )

    async def signal_message(self, event):
        data = event['data']
        if data.get('sender_channel') != self.channel_name:
            await self.send(text_data=json.dumps(data))

    async def user_joined(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user-joined',
            'user': event['user'],
            'channel': event['channel'],
        }))

    async def user_left(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user-left',
            'user': event['user'],
            'channel': event['channel'],
        }))
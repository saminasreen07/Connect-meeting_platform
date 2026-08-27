import json
import os
import re
import asyncio
import uuid
import time
import logging
import requests
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)

# Structured room presence tracking
# room_participants = {
#     room_group_name: {
#         user_id_str: {
#             'user_id': user_id_str,
#             'username': username,
#             'is_host': bool,
#             'channels': set(channel_names),
#             'raised_hand': bool,
#             'muted': bool
#         }
#     }
# }
room_participants = {}
channel_to_participant = {}  # {channel_name: (room_group_name, user_id_str)}
room_screen_sharers = {}     # {room_group_name: channel_name}


def is_valid_speech_text(text):
    """Filter out silence hallucinations, timing tokens (e.g. 00:00), and subtitle artifacts."""
    if not text:
        return False
    t = text.strip()
    if len(t) < 2:
        return False
    # Filter pure timestamps (e.g. 00:00, 00:00:00, 00:00:00.000 --> 00:00:03.000, 0:00)
    if re.search(r'^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$', t) or '-->' in t:
        return False
    lower_t = t.lower()
    junk = {
        '00:00', '0:00', 'webvtt', '[music]', '[applause]', '[silence]',
        '[blank_audio]', 'subtitle by', 'subtitles by', 'thank you for watching',
        'thanks for watching', 'thank you', 'thanks', 'you', 'none',
        'i am sorry', 'am sorry', 'the end', 'bye'
    }
    if lower_t in junk:
        return False
    cleaned = re.sub(r'[\d:\.\-\s\>\[\]\(\)]', '', t)
    if not cleaned:
        return False
    return True


class MeetingConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        try:
            self.room_code = self.scope['url_route']['kwargs']['room_code']
            self.room_group_name = f'meeting_{self.room_code}'
            self.user = self.scope['user']
            self.user_id = str(self.user.id) if (self.user and self.user.is_authenticated) else f"anon_{self.channel_name}"
            self.username = self.user.username if (self.user and self.user.is_authenticated) else 'Anonymous'

            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            await self.accept()

            # Initialize room directory if not present
            if self.room_group_name not in room_participants:
                room_participants[self.room_group_name] = {}

            # Sync database participant presence and determine host status
            self.is_host = await self.sync_participant_db(is_active=True)

            # Register user presence
            if self.user_id not in room_participants[self.room_group_name]:
                room_participants[self.room_group_name][self.user_id] = {
                    'user_id': self.user_id,
                    'username': self.username,
                    'is_host': self.is_host,
                    'channels': set(),
                    'raised_hand': False,
                    'muted': False,
                    'camera_on': True,
                }

            room_participants[self.room_group_name][self.user_id]['channels'].add(self.channel_name)
            channel_to_participant[self.channel_name] = (self.room_group_name, self.user_id)

            screen_sharer = room_screen_sharers.get(self.room_group_name, None)

            # Build deduplicated snapshot of existing active participants for the new connection
            existing_members = []
            for uid, p in room_participants[self.room_group_name].items():
                if uid != self.user_id:
                    # Provide active channels for WebRTC signaling
                    for ch in p['channels']:
                        existing_members.append({
                            'user_id': p['user_id'],
                            'user': p['username'],
                            'username': p['username'],
                            'is_host': p['is_host'],
                            'channel': ch,
                            'raised_hand': p['raised_hand'],
                            'muted': p['muted'],
                            'camera_on': p.get('camera_on', True),
                        })

            raised_hands_list = [
                p['username'] for p in room_participants[self.room_group_name].values() if p['raised_hand']
            ]

            # Send current room snapshot to the newly connected participant
            await self.send(text_data=json.dumps({
                'type': 'existing-members',
                'members': existing_members,
                'screen_sharer': screen_sharer,
                'raised_hands': raised_hands_list,
            }))

            # Send canonical meeting chat history to the newly connected participant
            chat_history = await self.get_chat_history()
            if chat_history:
                await self.send(text_data=json.dumps({
                    'type': 'chat-history',
                    'messages': chat_history,
                }))

            # Broadcast new participant arrival to all other room members
            join_event_id = f"join_{uuid.uuid4().hex[:8]}"
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_joined',
                    'event_id': join_event_id,
                    'user_id': self.user_id,
                    'user': self.username,
                    'username': self.username,
                    'is_host': self.is_host,
                    'channel': self.channel_name,
                    'timestamp': time.time(),
                }
            )
        except Exception as e:
            logger.error(f"[MeetingConsumer.connect] Error: {e}", exc_info=True)

    async def disconnect(self, close_code):
        try:
            mapping = channel_to_participant.pop(self.channel_name, None)
            is_last_connection = True

            if mapping:
                room_grp, uid = mapping
                if room_grp in room_participants and uid in room_participants[room_grp]:
                    room_participants[room_grp][uid]['channels'].discard(self.channel_name)
                    is_last_connection = len(room_participants[room_grp][uid]['channels']) == 0

                    if is_last_connection:
                        del room_participants[room_grp][uid]
                        if not room_participants[room_grp]:
                            del room_participants[room_grp]

                        # Sync database participant status to inactive only when last connection closes
                        if self.user and self.user.is_authenticated:
                            await self.sync_participant_db(is_active=False)

            # Clean up screen sharer state if this channel was sharing
            if room_screen_sharers.get(self.room_group_name) == self.channel_name:
                room_screen_sharers.pop(self.room_group_name, None)

            leave_event_id = f"leave_{uuid.uuid4().hex[:8]}"
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_left',
                    'event_id': leave_event_id,
                    'user_id': self.user_id,
                    'user': self.username,
                    'username': self.username,
                    'channel': self.channel_name,
                    'is_last_connection': is_last_connection,
                    'timestamp': time.time(),
                }
            )
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
        except Exception as e:
            logger.error(f"[MeetingConsumer.disconnect] Error: {e}", exc_info=True)

    @database_sync_to_async
    def sync_participant_db(self, is_active):
        from meetings.models import Meeting, Participant
        from django.utils import timezone
        try:
            meeting = Meeting.objects.get(room_code=self.room_code)
            is_host = (meeting.host_id == self.user.id) if (self.user and self.user.is_authenticated) else False
            if self.user and self.user.is_authenticated:
                if is_active:
                    Participant.objects.update_or_create(
                        meeting=meeting,
                        user=self.user,
                        defaults={'is_active': True, 'joined_at': timezone.now(), 'left_at': None}
                    )
                else:
                    Participant.objects.filter(
                        meeting=meeting,
                        user=self.user,
                        is_active=True
                    ).update(is_active=False, left_at=timezone.now())
            return is_host
        except Meeting.DoesNotExist:
            return False
        except Exception as e:
            logger.warning(f"[sync_participant_db] DB error: {e}")
            return False

    @database_sync_to_async
    def get_chat_history(self):
        from chat.models import ChatMessage
        try:
            msgs = list(ChatMessage.objects.filter(
                meeting__room_code=self.room_code
            ).order_by('timestamp').select_related('sender')[:150])
            return [
                {
                    'message_id': f"db-{m.id}",
                    'sender': m.sender.username,
                    'user_id': str(m.sender.id),
                    'message': m.message,
                    'timestamp': m.timestamp.timestamp(),
                }
                for m in msgs
            ]
        except Exception as e:
            logger.warning(f"[get_chat_history] Error: {e}")
            return []

    @database_sync_to_async
    def save_chat_message(self, message_text):
        from meetings.models import Meeting
        from chat.models import ChatMessage
        try:
            meeting = Meeting.objects.get(room_code=self.room_code)
            ChatMessage.objects.create(
                meeting=meeting,
                sender=self.user,
                message=message_text
            )
        except Meeting.DoesNotExist:
            pass
        except Exception as e:
            logger.warning(f"[save_chat_message] DB error: {e}")

    @database_sync_to_async
    def save_transcript_message(self, text, detected_language='en'):
        try:
            from meetings.models import Meeting, TranscriptMessage
            if not is_valid_speech_text(text):
                return
            meeting = Meeting.objects.get(room_code=self.room_code)
            TranscriptMessage.objects.create(
                meeting=meeting,
                speaker=self.user,
                text=text.strip(),
                language=detected_language or 'en',
                detected_language=detected_language or 'en'
            )
        except Meeting.DoesNotExist:
            pass
        except Exception as e:
            logger.warning(f"[save_transcript_message] DB error: {e}")

    async def transcribe_audio_data(self, audio_base64, mime_type='audio/webm'):
        import base64
        gemini_key = os.getenv('GEMINI_API_KEY')
        assemblyai_key = os.getenv('ASSEMBLYAI_API_KEY')

        def _blocking_call():
            if not audio_base64:
                return '', 'en'
            try:
                audio_bytes = base64.b64decode(audio_base64)
                if len(audio_bytes) < 400:
                    return '', 'en'
            except Exception:
                return '', 'en'

            clean_mime = mime_type.split(';')[0].strip() if mime_type else 'audio/webm'
            if clean_mime not in ['audio/webm', 'audio/mp4', 'audio/aac', 'audio/ogg', 'audio/wav', 'audio/mpeg', 'audio/flac']:
                clean_mime = 'audio/webm'

            if gemini_key and gemini_key.strip():
                for model_name in ['gemini-3.6-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.7-flash', 'gemini-flash-latest', 'gemini-2.0-flash', 'gemini-1.5-flash']:
                    try:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key.strip()}"
                        prompt = (
                            "Transcribe the spoken words in this audio in their original spoken language. "
                            "Do NOT output timestamps (no 00:00), no subtitle headers, no explanations. "
                            "If silence or no speech, return {\"text\": \"\", \"language\": \"en\"}. "
                            "Return ONLY valid JSON with keys 'text' and 'language'."
                        )
                        payload = {
                            "contents": [{
                                "parts": [
                                    {"inlineData": {"mimeType": clean_mime, "data": audio_base64}},
                                    {"text": prompt}
                                ]
                            }],
                            "generationConfig": {
                                "temperature": 0.1,
                                "maxOutputTokens": 300,
                            }
                        }
                        r = requests.post(url, json=payload, timeout=5)
                        if r.status_code == 200:
                            candidates = r.json().get('candidates', [])
                            if candidates:
                                parts = candidates[0].get('content', {}).get('parts', [])
                                if parts and parts[0].get('text'):
                                    raw_text = parts[0]['text'].strip()
                                    json_str = raw_text
                                    if '```' in json_str:
                                        json_str = json_str.split('```')[1]
                                        if json_str.startswith('json'):
                                            json_str = json_str[4:]
                                        json_str = json_str.strip()
                                    try:
                                        parsed = json.loads(json_str)
                                        t_text = parsed.get('text', '').strip()
                                        t_lang = parsed.get('language', 'auto').strip().lower()
                                        if is_valid_speech_text(t_text):
                                            return t_text, t_lang
                                    except Exception:
                                        if is_valid_speech_text(raw_text) and not raw_text.startswith('{'):
                                            return raw_text, 'auto'
                    except Exception:
                        continue

            if assemblyai_key and assemblyai_key.strip():
                try:
                    headers = {'authorization': assemblyai_key.strip()}
                    upload_res = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=audio_bytes, timeout=4)
                    if upload_res.status_code == 200:
                        audio_url = upload_res.json().get('upload_url')
                        if audio_url:
                            trans_payload = {'audio_url': audio_url, 'language_detection': True}
                            trans_res = requests.post('https://api.assemblyai.com/v2/transcript', headers=headers, json=trans_payload, timeout=4)
                            if trans_res.status_code == 200:
                                trans_id = trans_res.json().get('id')
                                if trans_id:
                                    for _ in range(2):
                                        import time
                                        time.sleep(1)
                                        poll_res = requests.get(f'https://api.assemblyai.com/v2/transcript/{trans_id}', headers=headers, timeout=3)
                                        if poll_res.status_code == 200:
                                            p_data = poll_res.json()
                                            if p_data.get('status') == 'completed':
                                                cand_text = p_data.get('text', '').strip()
                                                if is_valid_speech_text(cand_text):
                                                    return cand_text, p_data.get('language_code', 'en')
                                            elif p_data.get('status') == 'error':
                                                break
                except Exception:
                    pass

            return '', 'en'

        return await asyncio.to_thread(_blocking_call)

    async def _handle_audio_transcription(self, data):
        try:
            audio_b64 = data.get('audio', '')
            mime_type = data.get('mime_type', 'audio/webm')
            if not audio_b64:
                return
            transcribed_text, detected_lang = await self.transcribe_audio_data(audio_b64, mime_type=mime_type)
            if transcribed_text and is_valid_speech_text(transcribed_text):
                asyncio.create_task(self.save_transcript_message(transcribed_text, detected_language=detected_lang))
                event_id = data.get('event_id') or f"trans_{uuid.uuid4().hex[:8]}"
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'transcript_message',
                        'event_id': event_id,
                        'text': transcribed_text,
                        'speaker': self.username,
                        'sender': self.username,
                        'user_id': self.user_id,
                        'sender_channel': self.channel_name,
                        'detected_language': detected_lang,
                        'speaking_lang': detected_lang,
                        'lang': detected_lang,
                        'is_final': True,
                        'timestamp': time.time(),
                    }
                )
        except Exception as e:
            logger.error(f"[_handle_audio_transcription] Error: {e}", exc_info=True)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except Exception as e:
            logger.warning(f"[MeetingConsumer.receive] JSON decode error: {e}")
            return

        msg_type = data.get('type')

        if msg_type == 'ping':
            await self.send(text_data=json.dumps({
                'type': 'pong',
                'client_time': data.get('timestamp'),
                'server_time': time.time()
            }))
            return

        if msg_type == 'screen-share-started':
            room_screen_sharers[self.room_group_name] = self.channel_name
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'screen_share_started_message',
                    'sender_channel': self.channel_name,
                    'sender': self.username,
                    'user_id': self.user_id,
                    'timestamp': time.time(),
                }
            )
            return

        if msg_type == 'screen-share-ended':
            if room_screen_sharers.get(self.room_group_name) == self.channel_name:
                room_screen_sharers.pop(self.room_group_name, None)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'screen_share_ended_message',
                    'sender_channel': self.channel_name,
                    'sender': self.username,
                    'user_id': self.user_id,
                    'timestamp': time.time(),
                }
            )
            return

        if msg_type == 'get_chat_history':
            chat_history = await self.get_chat_history()
            await self.send(text_data=json.dumps({
                'type': 'chat-history',
                'messages': chat_history,
            }))
            return

        if msg_type == 'chat':
            message_text = data.get('message', '').strip()
            sender_lang = data.get('sender_lang', 'en')
            client_id = data.get('client_id', '')
            message_id = data.get('message_id') or data.get('event_id') or f"chat_{uuid.uuid4().hex[:8]}"
            if message_text:
                asyncio.create_task(self.save_chat_message(message_text))
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'message_id': message_id,
                        'event_id': message_id,
                        'message': message_text,
                        'sender': self.username,
                        'user_id': self.user_id,
                        'sender_channel': self.channel_name,
                        'sender_lang': sender_lang,
                        'client_id': client_id,
                        'timestamp': time.time(),
                    }
                )
            return

        if msg_type == 'mute_status':
            is_muted = bool(data.get('muted', False))
            if self.room_group_name in room_participants and self.user_id in room_participants[self.room_group_name]:
                room_participants[self.room_group_name][self.user_id]['muted'] = is_muted

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'mute_status_message',
                    'channel': self.channel_name,
                    'user': self.username,
                    'user_id': self.user_id,
                    'muted': is_muted,
                    'timestamp': time.time(),
                }
            )
            return

        if msg_type == 'camera_status':
            camera_on = bool(data.get('camera_on', True))
            if self.room_group_name in room_participants and self.user_id in room_participants[self.room_group_name]:
                room_participants[self.room_group_name][self.user_id]['camera_on'] = camera_on

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'camera_status_message',
                    'channel': self.channel_name,
                    'user': self.username,
                    'user_id': self.user_id,
                    'camera_on': camera_on,
                    'timestamp': time.time(),
                }
            )
            return

        if msg_type == 'typing':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'typing_message',
                    'user': self.username,
                    'user_id': self.user_id,
                    'is_typing': data.get('is_typing', False),
                }
            )
            return

        if msg_type == 'transcribe_audio':
            asyncio.create_task(self._handle_audio_transcription(data))
            return

        if msg_type == 'raise_hand':
            is_raised = bool(data.get('raised', True))
            event_id = data.get('event_id') or f"hand_{uuid.uuid4().hex[:8]}"
            if self.room_group_name in room_participants and self.user_id in room_participants[self.room_group_name]:
                room_participants[self.room_group_name][self.user_id]['raised_hand'] = is_raised

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'raise_hand_message',
                    'event_id': event_id,
                    'user': self.username,
                    'user_id': self.user_id,
                    'channel': self.channel_name,
                    'raised': is_raised,
                    'timestamp': time.time(),
                }
            )
            return

        if msg_type == 'reaction':
            reaction_id = data.get('reaction_id') or data.get('event_id') or f"react_{uuid.uuid4().hex[:8]}"
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'reaction_message',
                    'reaction_id': reaction_id,
                    'event_id': reaction_id,
                    'user': self.username,
                    'user_id': self.user_id,
                    'sender_channel': self.channel_name,
                    'emoji': data.get('emoji', '👍'),
                    'timestamp': time.time(),
                }
            )
            return

        if msg_type == 'transcript':
            text = data.get('text', '').strip()
            detected_lang = data.get('detected_language') or data.get('speaking_lang') or data.get('lang', 'auto')
            is_final = data.get('is_final', True)
            client_id = data.get('client_id', '')
            event_id = data.get('event_id') or f"tr_{uuid.uuid4().hex[:8]}"
            if text and is_valid_speech_text(text):
                if is_final:
                    asyncio.create_task(self.save_transcript_message(text, detected_language=detected_lang))
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'transcript_message',
                        'event_id': event_id,
                        'text': text,
                        'speaker': self.username,
                        'sender': self.username,
                        'user_id': self.user_id,
                        'sender_channel': self.channel_name,
                        'client_id': client_id,
                        'detected_language': detected_lang,
                        'speaking_lang': detected_lang,
                        'lang': detected_lang,
                        'is_final': is_final,
                        'timestamp': time.time(),
                    }
                )
            return

        # WebRTC signaling fallback
        data['sender_channel'] = self.channel_name
        data['sender'] = self.username
        data['user_id'] = self.user_id
        target = data.get('target')

        if target:
            await self.channel_layer.send(
                target,
                {
                    'type': 'signal_message',
                    'data': data,
                }
            )
        else:
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

    async def chat_message(self, event):
        if event.get('sender_channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'chat',
            'message_id': event.get('message_id', event.get('event_id', '')),
            'event_id': event.get('event_id', ''),
            'message': event['message'],
            'sender': event['sender'],
            'user_id': event.get('user_id', ''),
            'sender_lang': event.get('sender_lang', 'en'),
            'client_id': event.get('client_id', ''),
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def user_joined(self, event):
        if event.get('channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'user-joined',
            'event_id': event.get('event_id', ''),
            'user_id': event.get('user_id', ''),
            'user': event['user'],
            'username': event.get('username', event['user']),
            'is_host': event.get('is_host', False),
            'channel': event['channel'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def user_left(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user-left',
            'event_id': event.get('event_id', ''),
            'user_id': event.get('user_id', ''),
            'user': event['user'],
            'username': event.get('username', event['user']),
            'channel': event['channel'],
            'is_last_connection': event.get('is_last_connection', True),
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def mute_status_message(self, event):
        if event.get('channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'mute_status',
            'channel': event['channel'],
            'user_id': event.get('user_id', ''),
            'user': event['user'],
            'muted': event['muted'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def camera_status_message(self, event):
        if event.get('channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'camera_status',
            'channel': event['channel'],
            'user_id': event.get('user_id', ''),
            'user': event['user'],
            'camera_on': event['camera_on'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def transcript_message(self, event):
        if event.get('sender_channel') == self.channel_name:
            return
        detected_lang = event.get('detected_language') or event.get('speaking_lang', event.get('lang', 'auto'))
        await self.send(text_data=json.dumps({
            'type': 'transcript',
            'event_id': event.get('event_id', ''),
            'text': event['text'],
            'speaker': event.get('speaker', event.get('sender', 'Unknown')),
            'sender': event['sender'],
            'user_id': event.get('user_id', ''),
            'client_id': event.get('client_id', ''),
            'detected_language': detected_lang,
            'speaking_lang': detected_lang,
            'lang': detected_lang,
            'is_final': event.get('is_final', True),
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def meeting_ended(self, event):
        await self.send(text_data=json.dumps({
            'type': 'meeting_ended',
            'timestamp': time.time(),
        }))

    async def screen_share_started_message(self, event):
        if event.get('channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'screen-share-started',
            'channel': event['sender_channel'],
            'user_id': event.get('user_id', ''),
            'user': event['sender'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def screen_share_ended_message(self, event):
        if event.get('channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'screen-share-ended',
            'channel': event['sender_channel'],
            'user_id': event.get('user_id', ''),
            'user': event['sender'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def raise_hand_message(self, event):
        if event.get('channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'raise_hand',
            'event_id': event.get('event_id', ''),
            'user_id': event.get('user_id', ''),
            'user': event['user'],
            'channel': event.get('channel', ''),
            'raised': event['raised'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def reaction_message(self, event):
        if event.get('sender_channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'reaction',
            'reaction_id': event.get('reaction_id', event.get('event_id', '')),
            'event_id': event.get('event_id', ''),
            'user_id': event.get('user_id', ''),
            'user': event['user'],
            'channel': event.get('sender_channel', ''),
            'emoji': event['emoji'],
            'timestamp': event.get('timestamp', time.time()),
        }))

    async def typing_message(self, event):
        if event['user'] != self.username:
            await self.send(text_data=json.dumps({
                'type': 'typing',
                'user_id': event.get('user_id', ''),
                'user': event['user'],
                'is_typing': event['is_typing'],
            }))
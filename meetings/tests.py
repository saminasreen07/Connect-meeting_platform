from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from meetings.models import Meeting, Participant, TranscriptMessage
from chat.models import ChatMessage
from ai_insights.models import AIInsight
import json

User = get_user_model()

class MeetingViewsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.host = User.objects.create_user(username='hostuser', password='password123')
        self.meeting = Meeting.objects.create(
            host=self.host,
            title='Room Test Meeting',
            is_active=True
        )

    def test_create_meeting_requires_login(self):
        url = reverse('create_meeting')
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)

    def test_create_meeting_logged_in(self):
        self.client.login(username='testuser', password='password123')
        url = reverse('create_meeting')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url, {'title': 'New Meeting'})
        self.assertEqual(response.status_code, 302)
        new_meeting = Meeting.objects.latest('created_at')
        self.assertEqual(new_meeting.title, 'New Meeting')
        self.assertEqual(new_meeting.host, self.user)

    def test_join_meeting_logged_in(self):
        self.client.login(username='testuser', password='password123')
        url = reverse('join_meeting')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url, {'room_code': self.meeting.room_code})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Participant.objects.filter(meeting=self.meeting, user=self.user).exists())

    def test_meeting_room_loads_history(self):
        msg = ChatMessage.objects.create(
            meeting=self.meeting,
            sender=self.host,
            message='Old chat message'
        )

        self.client.login(username='testuser', password='password123')
        Participant.objects.create(meeting=self.meeting, user=self.user)
        
        url = reverse('meeting_room', kwargs={'room_code': self.meeting.room_code})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Old chat message')
        self.assertIn(msg, response.context['chat_messages'])

    def test_delete_meeting_as_host(self):
        self.client.login(username='hostuser', password='password123')
        url = reverse('delete_meeting', kwargs={'room_code': self.meeting.room_code})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Meeting.objects.filter(room_code=self.meeting.room_code).exists())

    def test_delete_meeting_as_non_host(self):
        self.client.login(username='testuser', password='password123')
        url = reverse('delete_meeting', kwargs={'room_code': self.meeting.room_code})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Meeting.objects.filter(room_code=self.meeting.room_code).exists())

    def test_translate_endpoint(self):
        self.client.login(username='testuser', password='password123')
        url = reverse('translate_text')
        response = self.client.post(
            url, 
            json.dumps({'text': 'Hello', 'source_lang': 'en', 'target_lang': 'es'}), 
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('translated_text', data)

    def test_translate_same_language_returns_original(self):
        url = reverse('translate_text')
        response = self.client.post(
            url,
            json.dumps({'text': 'Good morning', 'source_lang': 'en', 'target_lang': 'en'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('translated_text'), 'Good morning')

    def test_translate_empty_text(self):
        url = reverse('translate_text')
        response = self.client.post(
            url,
            json.dumps({'text': '', 'source_lang': 'ta', 'target_lang': 'en'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('translated_text'), '')

    def test_transcript_message_detected_language(self):
        t1 = TranscriptMessage.objects.create(
            meeting=self.meeting,
            speaker=self.host,
            text='வணக்கம் நண்பர்களே',
            language='ta',
            detected_language='ta'
        )
        self.assertEqual(t1.detected_language, 'ta')
        self.assertEqual(t1.speaker.username, 'hostuser')

    def test_generate_insights(self):
        self.client.login(username='hostuser', password='password123')
        Participant.objects.create(meeting=self.meeting, user=self.host)
        
        TranscriptMessage.objects.create(
            meeting=self.meeting,
            speaker=self.host,
            text='We need to complete this task today.',
            detected_language='en'
        )
        
        url = reverse('generate_insights', kwargs={'room_code': self.meeting.room_code})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        
        self.assertTrue(AIInsight.objects.filter(meeting=self.meeting).exists())
        insight = AIInsight.objects.get(meeting=self.meeting)
        self.assertTrue(len(insight.summary) > 0)


from channels.testing import WebsocketCommunicator
from core.asgi import application

class MeetingConsumerWebSocketTests(TestCase):
    async def test_websocket_lifecycle_and_events(self):
        user1 = await User.objects.acreate_user(username='alice', password='password123')
        user2 = await User.objects.acreate_user(username='bob', password='password123')
        meeting = await Meeting.objects.acreate(host=user1, title='Realtime Test Meeting', is_active=True)

        # 1. Connect user1
        comm1 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm1.scope['user'] = user1
        connected1, _ = await comm1.connect()
        self.assertTrue(connected1)

        # Receive existing-members
        msg1 = await comm1.receive_json_from()
        self.assertEqual(msg1['type'], 'existing-members')

        # 2. Ping-Pong Heartbeat
        await comm1.send_json_to({'type': 'ping', 'timestamp': 12345})
        pong_msg = await comm1.receive_json_from()
        self.assertEqual(pong_msg['type'], 'pong')
        self.assertEqual(pong_msg['client_time'], 12345)

        # 3. Connect user2
        comm2 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm2.scope['user'] = user2
        connected2, _ = await comm2.connect()
        self.assertTrue(connected2)

        # user2 receives existing-members containing alice
        msg2 = await comm2.receive_json_from()
        self.assertEqual(msg2['type'], 'existing-members')
        self.assertTrue(any(m['user'] == 'alice' for m in msg2['members']))

        # user1 receives user-joined for bob
        join_msg = await comm1.receive_json_from()
        self.assertEqual(join_msg['type'], 'user-joined')
        self.assertEqual(join_msg['user'], 'bob')
        self.assertTrue('event_id' in join_msg)

        # 4. Chat Message from Bob
        await comm2.send_json_to({
            'type': 'chat',
            'event_id': 'chat_test_123',
            'message': 'Hello Alice!',
            'client_id': 'client_bob_1'
        })

        chat_msg = await comm1.receive_json_from()
        self.assertEqual(chat_msg['type'], 'chat')
        self.assertEqual(chat_msg['message'], 'Hello Alice!')
        self.assertEqual(chat_msg['sender'], 'bob')
        self.assertEqual(chat_msg['event_id'], 'chat_test_123')

        # 5. Raise Hand from Alice
        await comm1.send_json_to({
            'type': 'raise_hand',
            'event_id': 'hand_test_123',
            'raised': True
        })

        hand_msg2 = await comm2.receive_json_from()
        self.assertEqual(hand_msg2['type'], 'raise_hand')
        self.assertEqual(hand_msg2['user'], 'alice')
        self.assertTrue(hand_msg2['raised'])

        # 6. Reaction from Bob
        await comm2.send_json_to({
            'type': 'reaction',
            'event_id': 'react_test_123',
            'emoji': '🔥'
        })

        react_msg1 = await comm1.receive_json_from()
        self.assertEqual(react_msg1['type'], 'reaction')
        self.assertEqual(react_msg1['emoji'], '🔥')
        self.assertEqual(react_msg1['user'], 'bob')

        # 7. Disconnect Bob
        await comm2.disconnect()

        leave_msg = await comm1.receive_json_from()
        self.assertEqual(leave_msg['type'], 'user-left')
        self.assertEqual(leave_msg['user'], 'bob')
        self.assertTrue(leave_msg.get('is_last_connection'))

        # Disconnect Alice
        await comm1.disconnect()

    async def test_refresh_reconnect_reference_counting(self):
        user = await User.objects.acreate_user(username='sadhana', password='password123')
        meeting = await Meeting.objects.acreate(host=user, title='Reconnect Meeting', is_active=True)

        # Tab 1 connects
        tab1 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        tab1.scope['user'] = user
        connected1, _ = await tab1.connect()
        self.assertTrue(connected1)
        await tab1.receive_json_from()  # existing-members

        # Tab 2 connects (simulating page refresh where new connection opens before old one tears down)
        tab2 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        tab2.scope['user'] = user
        connected2, _ = await tab2.connect()
        self.assertTrue(connected2)
        await tab2.receive_json_from()  # existing-members

        # Tab 1 disconnects (old connection closes)
        await tab1.disconnect()

        # Check that Participant is STILL active in the database because Tab 2 is still open
        participant = await Participant.objects.aget(meeting=meeting, user=user)
        self.assertTrue(participant.is_active)

        # Now Tab 2 disconnects
        await tab2.disconnect()

        # Now Participant should be marked inactive in the database
        participant = await Participant.objects.aget(meeting=meeting, user=user)
        self.assertFalse(participant.is_active)

    async def test_refresh_scenario_sadhana_nasreen(self):
        sadhana = await User.objects.acreate_user(username='sadhana_ref', password='password123')
        nasreen = await User.objects.acreate_user(username='nasreen_ref', password='password123')
        meeting = await Meeting.objects.acreate(host=sadhana, title='Sadhana & Nasreen Room', is_active=True)

        # 1. Sadhana joins (Host)
        sadhana_tab1 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        sadhana_tab1.scope['user'] = sadhana
        await sadhana_tab1.connect()
        await sadhana_tab1.receive_json_from()  # existing-members

        # 2. Nasreen joins (Participant)
        nasreen_tab = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        nasreen_tab.scope['user'] = nasreen
        await nasreen_tab.connect()
        nasreen_existing = await nasreen_tab.receive_json_from()
        self.assertEqual(len(nasreen_existing['members']), 1)
        self.assertEqual(nasreen_existing['members'][0]['username'], 'sadhana_ref')
        self.assertTrue(nasreen_existing['members'][0]['is_host'])
        self.assertEqual(nasreen_existing['members'][0]['user_id'], str(sadhana.id))

        await sadhana_tab1.receive_json_from()  # user-joined for nasreen

        # 3. Sadhana refreshes browser: Tab 2 connects before Tab 1 disconnects
        sadhana_tab2 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        sadhana_tab2.scope['user'] = sadhana
        await sadhana_tab2.connect()
        sadhana_tab2_existing = await sadhana_tab2.receive_json_from()
        self.assertEqual(len(sadhana_tab2_existing['members']), 1)
        self.assertEqual(sadhana_tab2_existing['members'][0]['username'], 'nasreen_ref')
        self.assertFalse(sadhana_tab2_existing['members'][0]['is_host'])

        # Nasreen receives user-joined for sadhana's new tab
        join_notif = await nasreen_tab.receive_json_from()
        self.assertEqual(join_notif['user_id'], str(sadhana.id))
        self.assertEqual(join_notif['username'], 'sadhana_ref')

        # Sadhana's old tab disconnects
        await sadhana_tab1.disconnect()

        # Nasreen receives user-left with is_last_connection = False
        leave_notif = await nasreen_tab.receive_json_from()
        self.assertEqual(leave_notif['user_id'], str(sadhana.id))
        self.assertFalse(leave_notif['is_last_connection'])

        # Both remain active in DB
        self.assertEqual(await Participant.objects.filter(meeting=meeting, is_active=True).acount(), 2)

        # Clean teardown
        await sadhana_tab2.disconnect()
        await nasreen_tab.disconnect()

    async def test_multi_participant_presence_5_users(self):
        users = []
        for i in range(5):
            u = await User.objects.acreate_user(username=f'user_{i}', password='password123')
            users.append(u)

        meeting = await Meeting.objects.acreate(host=users[0], title='5 Users Room', is_active=True)
        communicators = []

        for i, u in enumerate(users):
            comm = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
            comm.scope['user'] = u
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            communicators.append(comm)

            # Receive existing-members
            existing = await comm.receive_json_from()
            # For user i, should receive snapshot of all previous (i) users
            self.assertEqual(len(existing['members']), i)

            # Drain join broadcasts sent to earlier connected peers
            for prev_comm in communicators[:-1]:
                join_msg = await prev_comm.receive_json_from()
                self.assertEqual(join_msg['type'], 'user-joined')
                self.assertEqual(join_msg['user_id'], str(u.id))

        # Check DB active participant count
        self.assertEqual(await Participant.objects.filter(meeting=meeting, is_active=True).acount(), 5)

        # Disconnect user_4
        await communicators[4].disconnect()
        for comm in communicators[:4]:
            left_msg = await comm.receive_json_from()
            self.assertEqual(left_msg['type'], 'user-left')
            self.assertEqual(left_msg['user_id'], str(users[4].id))
            self.assertTrue(left_msg['is_last_connection'])

        self.assertEqual(await Participant.objects.filter(meeting=meeting, is_active=True).acount(), 4)

        # Cleanup remainder
        for comm in communicators[:4]:
            await comm.disconnect()

    async def test_webrtc_signaling_and_camera_mute_controls(self):
        sadhana = await User.objects.acreate_user(username='sadhana_rtc', password='password123')
        nasreen = await User.objects.acreate_user(username='nasreen_rtc', password='password123')
        meeting = await Meeting.objects.acreate(host=sadhana, title='WebRTC Room', is_active=True)

        # 1. Sadhana connects
        comm_sadhana = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_sadhana.scope['user'] = sadhana
        connected_s, _ = await comm_sadhana.connect()
        self.assertTrue(connected_s)
        await comm_sadhana.receive_json_from()  # existing-members

        # 2. Nasreen connects
        comm_nasreen = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_nasreen.scope['user'] = nasreen
        connected_n, _ = await comm_nasreen.connect()
        self.assertTrue(connected_n)

        # Nasreen gets snapshot containing Sadhana's channel
        nasreen_snapshot = await comm_nasreen.receive_json_from()
        self.assertEqual(len(nasreen_snapshot['members']), 1)
        sadhana_channel = nasreen_snapshot['members'][0]['channel']
        self.assertTrue(nasreen_snapshot['members'][0].get('camera_on', True))

        # Sadhana receives user-joined for Nasreen
        join_msg = await comm_sadhana.receive_json_from()
        nasreen_channel = join_msg['channel']

        # 3. Nasreen sends WebRTC SDP Offer to Sadhana
        offer_sdp = {'type': 'offer', 'sdp': 'v=0\r\no=- 1234 2 IN IP4 127.0.0.1...'}
        await comm_nasreen.send_json_to({
            'type': 'signal',
            'target': sadhana_channel,
            'signal': offer_sdp
        })

        # Sadhana receives targeted offer
        sadhana_signal = await comm_sadhana.receive_json_from()
        self.assertEqual(sadhana_signal['type'], 'signal')
        self.assertEqual(sadhana_signal['signal'], offer_sdp)
        self.assertEqual(sadhana_signal['sender_channel'], nasreen_channel)
        self.assertEqual(sadhana_signal['user_id'], str(nasreen.id))

        # 4. Sadhana sends WebRTC SDP Answer back to Nasreen
        answer_sdp = {'type': 'answer', 'sdp': 'v=0\r\no=- 5678 2 IN IP4 127.0.0.1...'}
        await comm_sadhana.send_json_to({
            'type': 'signal',
            'target': nasreen_channel,
            'signal': answer_sdp
        })

        # Nasreen receives targeted answer
        nasreen_signal = await comm_nasreen.receive_json_from()
        self.assertEqual(nasreen_signal['type'], 'signal')
        self.assertEqual(nasreen_signal['signal'], answer_sdp)
        self.assertEqual(nasreen_signal['sender_channel'], sadhana_channel)
        self.assertEqual(nasreen_signal['user_id'], str(sadhana.id))

        # 5. ICE Candidate Exchange
        ice_cand = {'candidate': 'candidate:1 1 UDP 2130706431 192.168.1.1 50000 typ host', 'sdpMid': '0'}
        await comm_nasreen.send_json_to({
            'type': 'signal',
            'target': sadhana_channel,
            'signal': ice_cand
        })
        sadhana_ice = await comm_sadhana.receive_json_from()
        self.assertEqual(sadhana_ice['signal'], ice_cand)

        # 6. Camera Status Toggle: Sadhana turns camera OFF
        await comm_sadhana.send_json_to({'type': 'camera_status', 'camera_on': False})
        cam_off_msg = await comm_nasreen.receive_json_from()
        self.assertEqual(cam_off_msg['type'], 'camera_status')
        self.assertEqual(cam_off_msg['user_id'], str(sadhana.id))
        self.assertFalse(cam_off_msg['camera_on'])

        # Sadhana turns camera ON
        await comm_sadhana.send_json_to({'type': 'camera_status', 'camera_on': True})
        cam_on_msg = await comm_nasreen.receive_json_from()
        self.assertEqual(cam_on_msg['type'], 'camera_status')
        self.assertTrue(cam_on_msg['camera_on'])

        # 7. Mic Mute Status Toggle: Nasreen mutes mic
        await comm_nasreen.send_json_to({'type': 'mute_status', 'muted': True})
        mute_msg = await comm_sadhana.receive_json_from()
        self.assertEqual(mute_msg['type'], 'mute_status')
        self.assertEqual(mute_msg['user_id'], str(nasreen.id))
        self.assertTrue(mute_msg['muted'])

        await comm_sadhana.disconnect()
        await comm_nasreen.disconnect()

    async def test_chat_emoji_reactions_and_raise_hand_realtime(self):
        user_a = await User.objects.acreate_user(username='alice_rt', password='password123')
        user_b = await User.objects.acreate_user(username='bob_rt', password='password123')
        user_c = await User.objects.acreate_user(username='charlie_rt', password='password123')
        meeting = await Meeting.objects.acreate(host=user_a, title='Realtime Interaction Room', is_active=True)

        # Connect Alice
        comm_a = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_a.scope['user'] = user_a
        await comm_a.connect()
        await comm_a.receive_json_from()  # existing-members

        # Connect Bob
        comm_b = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_b.scope['user'] = user_b
        await comm_b.connect()
        await comm_b.receive_json_from()  # existing-members
        await comm_a.receive_json_from()  # user-joined for Bob

        # Connect Charlie
        comm_c = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_c.scope['user'] = user_c
        await comm_c.connect()
        await comm_c.receive_json_from()  # existing-members
        await comm_a.receive_json_from()  # user-joined for Charlie
        await comm_b.receive_json_from()  # user-joined for Charlie

        # 1. CHAT: A -> B and C
        msg_id_1 = 'chat_test_001'
        await comm_a.send_json_to({
            'type': 'chat',
            'message_id': msg_id_1,
            'event_id': msg_id_1,
            'message': 'Hello everyone from Alice!',
            'client_id': 'client_a'
        })

        # Bob receives message 1
        chat_b = await comm_b.receive_json_from()
        self.assertEqual(chat_b['type'], 'chat')
        self.assertEqual(chat_b['message_id'], msg_id_1)
        self.assertEqual(chat_b['message'], 'Hello everyone from Alice!')
        self.assertEqual(chat_b['sender'], 'alice_rt')

        # Charlie receives message 1
        chat_c = await comm_c.receive_json_from()
        self.assertEqual(chat_c['type'], 'chat')
        self.assertEqual(chat_c['message_id'], msg_id_1)
        self.assertEqual(chat_c['message'], 'Hello everyone from Alice!')
        self.assertEqual(chat_c['sender'], 'alice_rt')

        # Sender Alice does NOT receive their own message broadcast
        self.assertTrue(await comm_a.receive_nothing())

        # CHAT: B -> A and C
        msg_id_2 = 'chat_test_002'
        await comm_b.send_json_to({
            'type': 'chat',
            'message_id': msg_id_2,
            'event_id': msg_id_2,
            'message': 'Hey Alice!',
            'client_id': 'client_b'
        })
        chat_a = await comm_a.receive_json_from()
        self.assertEqual(chat_a['type'], 'chat')
        self.assertEqual(chat_a['message_id'], msg_id_2)
        self.assertEqual(chat_a['message'], 'Hey Alice!')
        self.assertEqual(chat_a['sender'], 'bob_rt')

        chat_c2 = await comm_c.receive_json_from()
        self.assertEqual(chat_c2['type'], 'chat')
        self.assertEqual(chat_c2['message_id'], msg_id_2)
        self.assertEqual(chat_c2['message'], 'Hey Alice!')

        # 2. EMOJI REACTIONS: A sends 👍 -> B and C receive
        react_id_1 = 'react_test_001'
        await comm_a.send_json_to({
            'type': 'reaction',
            'reaction_id': react_id_1,
            'event_id': react_id_1,
            'emoji': '👍'
        })

        react_b = await comm_b.receive_json_from()
        self.assertEqual(react_b['type'], 'reaction')
        self.assertEqual(react_b['reaction_id'], react_id_1)
        self.assertEqual(react_b['emoji'], '👍')
        self.assertEqual(react_b['user'], 'alice_rt')

        react_c = await comm_c.receive_json_from()
        self.assertEqual(react_c['type'], 'reaction')
        self.assertEqual(react_c['reaction_id'], react_id_1)
        self.assertEqual(react_c['emoji'], '👍')
        self.assertEqual(react_c['user'], 'alice_rt')

        # Sender Alice does NOT receive reaction broadcast echo
        self.assertTrue(await comm_a.receive_nothing())

        # 3. RAISE HAND: A raises hand -> B and C immediately receive
        hand_id_1 = 'hand_test_001'
        await comm_a.send_json_to({
            'type': 'raise_hand',
            'event_id': hand_id_1,
            'raised': True
        })

        hand_b = await comm_b.receive_json_from()
        self.assertEqual(hand_b['type'], 'raise_hand')
        self.assertEqual(hand_b['user_id'], str(user_a.id))
        self.assertEqual(hand_b['user'], 'alice_rt')
        self.assertTrue(hand_b['raised'])

        hand_c = await comm_c.receive_json_from()
        self.assertEqual(hand_c['type'], 'raise_hand')
        self.assertEqual(hand_c['user_id'], str(user_a.id))
        self.assertTrue(hand_c['raised'])

        # A lowers hand -> B and C immediately receive
        hand_id_2 = 'hand_test_002'
        await comm_a.send_json_to({
            'type': 'raise_hand',
            'event_id': hand_id_2,
            'raised': False
        })

        hand_b2 = await comm_b.receive_json_from()
        self.assertEqual(hand_b2['type'], 'raise_hand')
        self.assertEqual(hand_b2['user_id'], str(user_a.id))
        self.assertFalse(hand_b2['raised'])

        hand_c2 = await comm_c.receive_json_from()
        self.assertEqual(hand_c2['type'], 'raise_hand')
        self.assertEqual(hand_c2['user_id'], str(user_a.id))
        self.assertFalse(hand_c2['raised'])

        # Disconnect all
        await comm_a.disconnect()
        await comm_b.disconnect()
        await comm_c.disconnect()

    async def test_speech_transcription_speaker_attribution_and_multilingual_captions(self):
        sadhana = await User.objects.acreate_user(username='sadhana_tr', password='password123')
        nasreen = await User.objects.acreate_user(username='nasreen_tr', password='password123')
        meeting = await Meeting.objects.acreate(host=sadhana, title='Transcription Test Meeting', is_active=True)

        comm_sadhana = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_sadhana.scope['user'] = sadhana
        await comm_sadhana.connect()
        await comm_sadhana.receive_json_from()  # existing-members

        comm_nasreen = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_nasreen.scope['user'] = nasreen
        await comm_nasreen.connect()
        await comm_nasreen.receive_json_from()  # existing-members
        await comm_sadhana.receive_json_from()  # user-joined for Nasreen

        # 1. Sadhana speaks English -> Nasreen receives transcript with speaker='sadhana_tr'
        event_id_1 = 'tr_test_sadhana_001'
        speech_text_1 = 'Hello everyone welcome to the meeting.'
        await comm_sadhana.send_json_to({
            'type': 'transcript',
            'event_id': event_id_1,
            'text': speech_text_1,
            'detected_language': 'en',
            'is_final': True,
            'client_id': 'sadhana_client'
        })

        tr_msg_nasreen = await comm_nasreen.receive_json_from()
        self.assertEqual(tr_msg_nasreen['type'], 'transcript')
        self.assertEqual(tr_msg_nasreen['speaker'], 'sadhana_tr')
        self.assertEqual(tr_msg_nasreen['user_id'], str(sadhana.id))
        self.assertEqual(tr_msg_nasreen['text'], speech_text_1)
        self.assertEqual(tr_msg_nasreen['detected_language'], 'en')

        # 2. Nasreen speaks Tamil/Hindi -> Sadhana receives transcript with speaker='nasreen_tr'
        event_id_2 = 'tr_test_nasreen_001'
        speech_text_2 = 'நன்றி நாம் தொடங்கலாம்.'
        await comm_nasreen.send_json_to({
            'type': 'transcript',
            'event_id': event_id_2,
            'text': speech_text_2,
            'detected_language': 'ta',
            'is_final': True,
            'client_id': 'nasreen_client'
        })

        tr_msg_sadhana = await comm_sadhana.receive_json_from()
        self.assertEqual(tr_msg_sadhana['type'], 'transcript')
        self.assertEqual(tr_msg_sadhana['speaker'], 'nasreen_tr')
        self.assertEqual(tr_msg_sadhana['user_id'], str(nasreen.id))
        self.assertEqual(tr_msg_sadhana['text'], speech_text_2)
        self.assertEqual(tr_msg_sadhana['detected_language'], 'ta')

        # 3. Test Silence / Hallucination Rejection
        from .consumers import is_valid_speech_text
        self.assertFalse(is_valid_speech_text(''))
        self.assertFalse(is_valid_speech_text('00:00'))
        self.assertFalse(is_valid_speech_text('00:00:01.000 --> 00:00:04.000'))
        self.assertFalse(is_valid_speech_text('[music]'))
        self.assertFalse(is_valid_speech_text('[blank_audio]'))
        self.assertFalse(is_valid_speech_text('Thank you for watching'))
        self.assertFalse(is_valid_speech_text('Subtitle by'))
        self.assertTrue(is_valid_speech_text('Let us review the architectural diagram.'))

        # 4. Verify Meeting Transcript Database Isolation
        from .models import TranscriptMessage
        import asyncio
        await asyncio.sleep(0.5)
        transcripts = [t async for t in TranscriptMessage.objects.filter(meeting=meeting).select_related('speaker')]
        self.assertTrue(len(transcripts) >= 2)
        self.assertEqual(transcripts[0].speaker_id, sadhana.id)
        self.assertEqual(transcripts[1].speaker_id, nasreen.id)

        await comm_sadhana.disconnect()
        await comm_nasreen.disconnect()


class AIInsightsViewTests(TestCase):

    def setUp(self):
        self.host = User.objects.create_user(username='insight_host', password='password123')
        self.participant = User.objects.create_user(username='insight_participant', password='password123')
        self.outsider = User.objects.create_user(username='insight_outsider', password='password123')
        self.meeting = Meeting.objects.create(host=self.host, title='Sprint Planning & Strategy', is_active=False)
        Participant.objects.create(meeting=self.meeting, user=self.participant, is_active=False)

        # Create canonical transcripts
        TranscriptMessage.objects.create(
            meeting=self.meeting,
            speaker=self.host,
            text='Welcome to our sprint planning session. We need to complete the backend authentication refactor.',
            language='en',
            detected_language='en'
        )
        TranscriptMessage.objects.create(
            meeting=self.meeting,
            speaker=self.participant,
            text='I will take the authentication refactor and finalize it by Monday.',
            language='en',
            detected_language='en'
        )
        TranscriptMessage.objects.create(
            meeting=self.meeting,
            speaker=self.host,
            text='We all agree on moving the deadline to next sprint.',
            language='en',
            detected_language='en'
        )

    def test_host_generates_ai_insights(self):
        self.client.login(username='insight_host', password='password123')
        response = self.client.get(reverse('generate_insights', kwargs={'room_code': self.meeting.room_code}))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('meeting_details', kwargs={'room_code': self.meeting.room_code}))

        from ai_insights.models import AIInsight
        insight = AIInsight.objects.get(meeting=self.meeting)
        self.assertIsNotNone(insight.summary)
        self.assertIn('Executive Summary', insight.summary)
        self.assertIn('Key Discussion Points', insight.summary)
        self.assertIn('Decisions & Conclusions', insight.summary)
        self.assertIn('Action Items', insight.summary)
        self.assertTrue(len(insight.speaker_stats) >= 2)

    def test_participant_generates_ai_insights(self):
        self.client.login(username='insight_participant', password='password123')
        response = self.client.get(reverse('generate_insights', kwargs={'room_code': self.meeting.room_code}))
        self.assertEqual(response.status_code, 302)

        from ai_insights.models import AIInsight
        insight = AIInsight.objects.get(meeting=self.meeting)
        self.assertIsNotNone(insight.summary)

    def test_outsider_forbidden_from_generating_insights(self):
        self.client.login(username='insight_outsider', password='password123')
        response = self.client.get(reverse('generate_insights', kwargs={'room_code': self.meeting.room_code}))
        self.assertEqual(response.status_code, 403)

    def test_empty_transcript_warning_redirect(self):
        empty_meeting = Meeting.objects.create(host=self.host, title='Empty Room', is_active=False)
        self.client.login(username='insight_host', password='password123')
        response = self.client.get(reverse('generate_insights', kwargs={'room_code': empty_meeting.room_code}))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('meeting_details', kwargs={'room_code': empty_meeting.room_code}))


class Phase7FullRegressionEndToEndTests(TestCase):

    async def test_full_end_to_end_regression_multi_user_meeting(self):
        # 1. Setup 4 Users: Sadhana (Host), Nasreen, Rahul, Priya
        sadhana = await User.objects.acreate_user(username='sadhana_reg', password='password123')
        nasreen = await User.objects.acreate_user(username='nasreen_reg', password='password123')
        rahul = await User.objects.acreate_user(username='rahul_reg', password='password123')
        priya = await User.objects.acreate_user(username='priya_reg', password='password123')

        meeting = await Meeting.objects.acreate(host=sadhana, title='Phase 7 Full Regression Meeting', is_active=True)

        # 2. Connect all 4 users sequentially
        comm_sadhana = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_sadhana.scope['user'] = sadhana
        await comm_sadhana.connect()
        snap_s = await comm_sadhana.receive_json_from()
        self.assertEqual(len(snap_s['members']), 0)

        comm_nasreen = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_nasreen.scope['user'] = nasreen
        await comm_nasreen.connect()
        snap_n = await comm_nasreen.receive_json_from()
        self.assertEqual(len(snap_n['members']), 1)
        join_n_for_s = await comm_sadhana.receive_json_from()
        self.assertEqual(join_n_for_s['user_id'], str(nasreen.id))

        comm_rahul = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_rahul.scope['user'] = rahul
        await comm_rahul.connect()
        snap_r = await comm_rahul.receive_json_from()
        self.assertEqual(len(snap_r['members']), 2)
        await comm_sadhana.receive_json_from()
        await comm_nasreen.receive_json_from()

        comm_priya = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_priya.scope['user'] = priya
        await comm_priya.connect()
        snap_p = await comm_priya.receive_json_from()
        self.assertEqual(len(snap_p['members']), 3)
        await comm_sadhana.receive_json_from()
        await comm_nasreen.receive_json_from()
        await comm_rahul.receive_json_from()

        # Check DB active participant count
        self.assertEqual(await Participant.objects.filter(meeting=meeting, is_active=True).acount(), 4)

        # 3. WebRTC Signaling: Nasreen sends Offer -> Sadhana sends Answer -> ICE candidate exchange
        sadhana_channel = snap_n['members'][0]['channel']
        offer_sdp = {'type': 'offer', 'sdp': 'v=0\r\nsdp_data...'}
        await comm_nasreen.send_json_to({'type': 'signal', 'target': sadhana_channel, 'signal': offer_sdp})
        sig_for_s = await comm_sadhana.receive_json_from()
        self.assertEqual(sig_for_s['type'], 'signal')
        self.assertEqual(sig_for_s['user_id'], str(nasreen.id))

        answer_sdp = {'type': 'answer', 'sdp': 'v=0\r\nsdp_answer_data...'}
        await comm_sadhana.send_json_to({'type': 'signal', 'target': sig_for_s['sender_channel'], 'signal': answer_sdp})
        sig_for_n = await comm_nasreen.receive_json_from()
        self.assertEqual(sig_for_n['type'], 'signal')
        self.assertEqual(sig_for_n['user_id'], str(sadhana.id))

        # 4. Camera & Mic controls without disconnecting
        await comm_sadhana.send_json_to({'type': 'camera_status', 'camera_on': False})
        cam_for_n = await comm_nasreen.receive_json_from()
        self.assertFalse(cam_for_n['camera_on'])
        await comm_rahul.receive_json_from()
        await comm_priya.receive_json_from()

        await comm_nasreen.send_json_to({'type': 'mute_status', 'muted': True})
        mute_for_s = await comm_sadhana.receive_json_from()
        self.assertTrue(mute_for_s['muted'])
        await comm_rahul.receive_json_from()
        await comm_priya.receive_json_from()

        # 5. Chat Messaging with deduplication
        chat_id_1 = 'chat_reg_001'
        await comm_sadhana.send_json_to({
            'type': 'chat',
            'message_id': chat_id_1,
            'message': 'Welcome team to sprint sync!',
            'client_id': 'sadhana_c'
        })
        c_n = await comm_nasreen.receive_json_from()
        c_r = await comm_rahul.receive_json_from()
        c_p = await comm_priya.receive_json_from()
        self.assertEqual(c_n['message_id'], chat_id_1)
        self.assertEqual(c_r['message_id'], chat_id_1)
        self.assertEqual(c_p['message_id'], chat_id_1)

        # 6. Emoji Reactions
        react_id_1 = 'react_reg_001'
        await comm_nasreen.send_json_to({'type': 'reaction', 'reaction_id': react_id_1, 'emoji': '❤️'})
        r_s = await comm_sadhana.receive_json_from()
        r_r = await comm_rahul.receive_json_from()
        r_p = await comm_priya.receive_json_from()
        self.assertEqual(r_s['emoji'], '❤️')
        self.assertEqual(r_r['emoji'], '❤️')
        self.assertEqual(r_p['emoji'], '❤️')

        # 7. Raise Hand: Rahul raises and lowers hand
        await comm_rahul.send_json_to({'type': 'raise_hand', 'raised': True})
        h_s = await comm_sadhana.receive_json_from()
        h_n = await comm_nasreen.receive_json_from()
        h_p = await comm_priya.receive_json_from()
        self.assertTrue(h_s['raised'])
        self.assertEqual(h_s['user_id'], str(rahul.id))

        await comm_rahul.send_json_to({'type': 'raise_hand', 'raised': False})
        h_s2 = await comm_sadhana.receive_json_from()
        self.assertFalse(h_s2['raised'])
        await comm_nasreen.receive_json_from()
        await comm_priya.receive_json_from()

        # 8. Speech Transcription & Multilingual Captions
        await comm_sadhana.send_json_to({
            'type': 'transcript',
            'event_id': 'tr_reg_001',
            'text': 'We agree to deploy the update tomorrow morning.',
            'detected_language': 'en',
            'is_final': True,
        })
        t_n = await comm_nasreen.receive_json_from()
        t_r = await comm_rahul.receive_json_from()
        t_p = await comm_priya.receive_json_from()
        self.assertEqual(t_n['speaker'], 'sadhana_reg')
        self.assertEqual(t_r['speaker'], 'sadhana_reg')
        self.assertEqual(t_p['speaker'], 'sadhana_reg')

        # 9. Refresh Lifecycle: Nasreen opens tab 2, closes tab 1
        comm_nasreen_tab2 = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_nasreen_tab2.scope['user'] = nasreen
        await comm_nasreen_tab2.connect()
        await comm_nasreen_tab2.receive_json_from()  # snapshot for tab 2
        # peers receive join with is_last_connection=False handled
        await comm_sadhana.receive_json_from()
        await comm_rahul.receive_json_from()
        await comm_priya.receive_json_from()

        # Tab 1 closes
        await comm_nasreen.disconnect()
        left_s = await comm_sadhana.receive_json_from()
        self.assertFalse(left_s['is_last_connection'])

        # Participant count remains 4 in DB
        self.assertEqual(await Participant.objects.filter(meeting=meeting, is_active=True).acount(), 4)

        # 10. Priya Leaves completely
        await comm_priya.disconnect()
        left_p = await comm_sadhana.receive_json_from()
        self.assertTrue(left_p['is_last_connection'])
        self.assertEqual(await Participant.objects.filter(meeting=meeting, is_active=True).acount(), 3)

        # Cleanup remainder
        await comm_sadhana.disconnect()
        await comm_nasreen_tab2.disconnect()
        await comm_rahul.disconnect()

    async def test_mobile_participant_audio_transcription_and_caption_delivery(self):
        sadhana = await User.objects.acreate_user(username='sadhana_desk', password='password123')
        nasreen = await User.objects.acreate_user(username='nasreen_mob', password='password123')

        meeting = await Meeting.objects.acreate(host=sadhana, title='Mobile Participant Sync', is_active=True)

        comm_sadhana = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_sadhana.scope['user'] = sadhana
        await comm_sadhana.connect()
        await comm_sadhana.receive_json_from()  # snapshot

        comm_nasreen = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_nasreen.scope['user'] = nasreen
        await comm_nasreen.connect()
        await comm_nasreen.receive_json_from()  # snapshot
        await comm_sadhana.receive_json_from()  # user-joined

        # 1. Desktop Sadhana speaks English -> Nasreen receives transcript
        await comm_sadhana.send_json_to({
            'type': 'transcript',
            'event_id': 'tr_desk_001',
            'text': 'Hello Nasreen, can you hear me on your mobile?',
            'detected_language': 'en',
            'is_final': True,
        })
        msg_for_nasreen = await comm_nasreen.receive_json_from()
        self.assertEqual(msg_for_nasreen['speaker'], 'sadhana_desk')
        self.assertEqual(msg_for_nasreen['text'], 'Hello Nasreen, can you hear me on your mobile?')

        # 2. Mobile Nasreen speaks -> Sends transcript (or audio chunks) -> Sadhana receives transcript
        await comm_nasreen.send_json_to({
            'type': 'transcript',
            'event_id': 'tr_mob_001',
            'text': 'Yes Sadhana, I can hear you clearly from my phone.',
            'detected_language': 'en',
            'is_final': True,
        })
        msg_for_sadhana = await comm_sadhana.receive_json_from()
        self.assertEqual(msg_for_sadhana['speaker'], 'nasreen_mob')
        self.assertEqual(msg_for_sadhana['user_id'], str(nasreen.id))
        self.assertEqual(msg_for_sadhana['text'], 'Yes Sadhana, I can hear you clearly from my phone.')

        # 3. Verify Database canonical transcript has both speakers
        from .models import TranscriptMessage
        import asyncio
        await asyncio.sleep(0.5)
        transcripts = [t async for t in TranscriptMessage.objects.filter(meeting=meeting).order_by('timestamp').select_related('speaker')]
        self.assertEqual(len(transcripts), 2)
        self.assertEqual(transcripts[0].speaker_id, sadhana.id)
        self.assertEqual(transcripts[1].speaker_id, nasreen.id)

        await comm_sadhana.disconnect()
        await comm_nasreen.disconnect()

    async def test_chat_instant_delivery_history_sync_and_mobile_resume(self):
        sadhana = await User.objects.acreate_user(username='sadhana_chat', password='password123')
        nasreen = await User.objects.acreate_user(username='nasreen_chat', password='password123')

        meeting = await Meeting.objects.acreate(host=sadhana, title='Chat Sync Meeting', is_active=True)

        comm_sadhana = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_sadhana.scope['user'] = sadhana
        await comm_sadhana.connect()
        await comm_sadhana.receive_json_from()  # snapshot

        comm_nasreen = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_nasreen.scope['user'] = nasreen
        await comm_nasreen.connect()
        await comm_nasreen.receive_json_from()  # snapshot
        await comm_sadhana.receive_json_from()  # user-joined

        # 1. Sadhana sends message -> Nasreen receives
        chat_id_1 = 'chat_sadhana_001'
        await comm_sadhana.send_json_to({
            'type': 'chat',
            'message_id': chat_id_1,
            'event_id': chat_id_1,
            'message': 'Hello Nasreen!',
            'client_id': 'sadhana_client'
        })
        msg_for_n = await comm_nasreen.receive_json_from()
        self.assertEqual(msg_for_n['type'], 'chat')
        self.assertEqual(msg_for_n['message'], 'Hello Nasreen!')
        self.assertEqual(msg_for_n['sender'], 'sadhana_chat')
        self.assertEqual(msg_for_n['message_id'], chat_id_1)

        # 2. Nasreen sends message -> Sadhana receives
        chat_id_2 = 'chat_nasreen_001'
        await comm_nasreen.send_json_to({
            'type': 'chat',
            'message_id': chat_id_2,
            'event_id': chat_id_2,
            'message': 'Hi Sadhana, I see your message!',
            'client_id': 'nasreen_client'
        })
        msg_for_s = await comm_sadhana.receive_json_from()
        self.assertEqual(msg_for_s['type'], 'chat')
        self.assertEqual(msg_for_s['message'], 'Hi Sadhana, I see your message!')
        self.assertEqual(msg_for_s['sender'], 'nasreen_chat')
        self.assertEqual(msg_for_s['message_id'], chat_id_2)

        # 3. Verify Database persistence
        from chat.models import ChatMessage
        import asyncio
        await asyncio.sleep(0.5)
        db_messages = [m async for m in ChatMessage.objects.filter(meeting=meeting).order_by('timestamp').select_related('sender')]
        self.assertEqual(len(db_messages), 2)
        self.assertEqual(db_messages[0].sender_id, sadhana.id)
        self.assertEqual(db_messages[0].message, 'Hello Nasreen!')
        self.assertEqual(db_messages[1].sender_id, nasreen.id)
        self.assertEqual(db_messages[1].message, 'Hi Sadhana, I see your message!')

        # 4. Nasreen mobile reconnects / resumes -> Requests chat history
        await comm_nasreen.send_json_to({'type': 'get_chat_history'})
        history_resp = await comm_nasreen.receive_json_from()
        self.assertEqual(history_resp['type'], 'chat-history')
        self.assertEqual(len(history_resp['messages']), 2)
        self.assertEqual(history_resp['messages'][0]['message'], 'Hello Nasreen!')
        self.assertEqual(history_resp['messages'][1]['message'], 'Hi Sadhana, I see your message!')

        # 5. New participant joins / user refreshes -> Receives chat history automatically on connect
        comm_refreshed = WebsocketCommunicator(application, f'/ws/meeting/{meeting.room_code}/')
        comm_refreshed.scope['user'] = nasreen
        await comm_refreshed.connect()
        await comm_refreshed.receive_json_from()  # existing-members
        hist_on_connect = await comm_refreshed.receive_json_from()  # chat-history
        self.assertEqual(hist_on_connect['type'], 'chat-history')
        self.assertEqual(len(hist_on_connect['messages']), 2)

        await comm_sadhana.disconnect()
        await comm_nasreen.disconnect()
        await comm_refreshed.disconnect()











from django.shortcuts import render, redirect, get_object_or_404
import os
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.contrib import messages
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Meeting, Participant, TranscriptMessage
from ai_insights.models import AIInsight
import json
import requests
from collections import Counter


@login_required
def create_meeting(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        if not title:
            title = 'Untitled Meeting'
        meeting = Meeting.objects.create(
            host=request.user,
            title=title,
            started_at=timezone.now()
        )
        Participant.objects.create(meeting=meeting, user=request.user)
        return redirect('meeting_created', room_code=meeting.room_code)
    return render(request, 'meetings/create_meeting.html')


@login_required
def meeting_created(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code, host=request.user)
    share_link = request.build_absolute_uri(reverse('meeting_room', args=[room_code]))
    return render(request, 'meetings/meeting_created.html', {
        'meeting': meeting,
        'room_code': room_code,
        'share_link': share_link,
    })


@login_required
def join_meeting(request):
    if request.method == 'POST':
        room_code = request.POST.get('room_code', '').strip().upper()
        if not room_code:
            messages.error(request, "Please enter a room code.")
            return render(request, 'meetings/join_meeting.html')
        try:
            meeting = Meeting.objects.get(room_code=room_code)
        except Meeting.DoesNotExist:
            messages.error(request, f"No meeting found with room code '{room_code}'. Please check the code and try again.")
            return render(request, 'meetings/join_meeting.html')
        if not meeting.is_active:
            messages.error(request, f"The meeting '{meeting.title}' has already ended. Please ask the host to start a new meeting.")
            return render(request, 'meetings/join_meeting.html')
        Participant.objects.get_or_create(meeting=meeting, user=request.user)
        return redirect('meeting_room', room_code=room_code)
    return render(request, 'meetings/join_meeting.html')


@login_required
def meeting_room(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code, is_active=True)

    # Auto-register/activate participant on direct link entry
    participant, created = Participant.objects.get_or_create(meeting=meeting, user=request.user)
    if not participant.is_active:
        participant.is_active = True
        participant.save()

    participants = meeting.participants.filter(is_active=True).select_related('user')
    chat_messages = meeting.chat_messages.select_related('sender').all()
    return render(request, 'meetings/room.html', {
        'meeting': meeting,
        'participants': participants,
        'room_code': room_code,
        'user': request.user,
        'is_host': meeting.host == request.user,
        'chat_messages': chat_messages,
    })


@login_required
def gladia_token(request):
    """Generates a temporary token/URL for Gladia WebSocket."""
    # Example logic - replace with your actual provider integration
    return JsonResponse({'token_url': 'wss://api.gladia.io/v2/live?language_behaviour=automatic'})


@login_required
def end_meeting(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code, host=request.user)
    meeting.is_active = False
    meeting.ended_at = timezone.now()
    meeting.save()
    meeting.participants.filter(is_active=True).update(is_active=False, left_at=timezone.now())

    # Broadcast to WebSocket room that meeting has ended
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'meeting_{room_code}',
        {'type': 'meeting_ended'}
    )
    return redirect('dashboard')


@login_required
def leave_meeting(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code)
    Participant.objects.filter(meeting=meeting, user=request.user).update(
        is_active=False, left_at=timezone.now()
    )
    return redirect('dashboard')


@login_required
def meeting_history(request):
    meetings = Meeting.objects.filter(participants__user=request.user).distinct().order_by('-created_at')
    return render(request, 'meetings/history.html', {'meetings': meetings})


@login_required
def meeting_details(request, room_code):
    from django.db.models import Q
    meeting = get_object_or_404(
        Meeting.objects.filter(Q(host=request.user) | Q(participants__user=request.user)).distinct(),
        room_code=room_code
    )
    chat_messages = meeting.chat_messages.select_related('sender').all()
    all_transcripts = meeting.transcripts.select_related('speaker').all()
    transcripts = [t for t in all_transcripts if is_valid_speech_text(t.text)]

    try:
        insight = meeting.ai_insight
    except AIInsight.DoesNotExist:
        insight = None

    return render(request, 'meetings/details.html', {
        'meeting': meeting,
        'chat_messages': chat_messages,
        'transcripts': transcripts,
        'insight': insight
    })


@login_required
def delete_meeting(request, room_code):
    meeting = get_object_or_404(Meeting, room_code=room_code)
    if meeting.host != request.user:
        return HttpResponseForbidden("You do not have permission to delete this meeting.")
    if request.method == 'POST':
        meeting.delete()
    return redirect('meeting_history')


@csrf_exempt
def translate_text(request):
    """
    Translate text into the display language using Gemini 3.6 Flash (with MyMemory fallback).
    Validation ensures:
    - Same-language pairs immediately return the original transcript without calling translation APIs.
    - Source language is auto-detected if not specified.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
        text = body.get('text', '').strip()
        source_lang = body.get('source_lang', 'auto')
        target_lang = body.get('target_lang', 'en')

        if not text:
            return JsonResponse({'translated_text': ''})

        # Normalize language codes (e.g. 'ta-IN' -> 'ta', 'en-US' -> 'en')
        src = '' if source_lang in ('auto', '', None) else source_lang.split('-')[0].lower()
        tgt = target_lang.split('-')[0].lower() if target_lang else 'en'

        # Safe validation: If both languages are the same, skip translation API call
        if src and src == tgt:
            return JsonResponse({'translated_text': text})

        from django.conf import settings as django_settings
        gemini_api_key = getattr(django_settings, 'GEMINI_API_KEY', '') or os.getenv('GEMINI_API_KEY', '')

        # Map language codes to human readable names for better prompt precision
        lang_names = {
            'en': 'English', 'ta': 'Tamil', 'hi': 'Hindi', 'ml': 'Malayalam',
            'te': 'Telugu', 'kn': 'Kannada', 'mr': 'Marathi', 'bn': 'Bengali',
            'gu': 'Gujarati', 'pa': 'Punjabi', 'ur': 'Urdu', 'es': 'Spanish',
            'fr': 'French', 'de': 'German', 'zh': 'Chinese', 'ar': 'Arabic',
            'ru': 'Russian', 'ja': 'Japanese', 'ko': 'Korean', 'pt': 'Portuguese',
            'it': 'Italian'
        }
        target_name = lang_names.get(tgt, tgt)
        source_name = lang_names.get(src, '')

        # ── PRIMARY: Gemini 3.6 Flash Translation ────────────────────────────
        if gemini_api_key and gemini_api_key.strip():
            src_desc = f" from {source_name}" if source_name else ""
            prompt = (
                f"Translate the following speech transcript{src_desc} into {target_name} ({tgt}). "
                f"Maintain natural phrasing and colloquial context. "
                f"Return ONLY the direct translated text. Do not include notes, quotes, or explanations:\n\n{text}"
            )
            for model_name in ['gemini-3.6-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.7-flash', 'gemini-flash-latest']:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_api_key.strip()}"
                    headers = {
                        'Content-Type': 'application/json'
                    }
                    payload = {
                        'contents': [{'parts': [{'text': prompt}]}],
                        'generationConfig': {
                            'temperature': 0.2,
                            'maxOutputTokens': 300,
                        }
                    }
                    r = requests.post(url, headers=headers, json=payload, timeout=5)
                    if r.status_code == 200:
                        res = r.json()
                        candidates = res.get('candidates', [])
                        if candidates:
                            parts = candidates[0].get('content', {}).get('parts', [])
                            if parts and parts[0].get('text'):
                                translated = parts[0]['text'].strip()
                                if translated:
                                    return JsonResponse({'translated_text': translated})
                except Exception as gemini_err:
                    continue

        # ── FALLBACK: MyMemory free API ──────────────────────────────────────
        lang_pair = f"{src}|{tgt}" if src else f"en|{tgt}"
        try:
            resp = requests.get(
                'https://api.mymemory.translated.net/get',
                params={'q': text, 'langpair': lang_pair},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                translated = data.get('responseData', {}).get('translatedText', '').strip()
                if translated and translated.upper() != 'INVALID LANGUAGE PAIR':
                    return JsonResponse({'translated_text': translated})
        except Exception:
            pass

        # If all fail, return original text safely
        return JsonResponse({'translated_text': text})

    except Exception as e:
        return JsonResponse({'translated_text': '', 'error': str(e)}, status=500)


def analyze_sentiment(text):
    positive_words = {'good', 'great', 'excellent', 'amazing', 'happy', 'yes', 'agree',
                      'perfect', 'thanks', 'thank', 'awesome', 'cool', 'done', 'resolved'}
    negative_words = {'bad', 'poor', 'terrible', 'no', 'disagree', 'fail', 'issue',
                      'problem', 'error', 'wrong', 'difficult', 'cannot', 'broken'}

    words = text.lower().split()
    pos_count = sum(1 for w in words if w in positive_words)
    neg_count = sum(1 for w in words if w in negative_words)

    if pos_count > neg_count:
        return 'Positive'
    elif neg_count > pos_count:
        return 'Negative'
    return 'Neutral'


import re

def is_valid_speech_text(text):
    """Filter out silence hallucinations, timing tokens (e.g. 00:00), and subtitle artifacts."""
    if not text:
        return False
    t = text.strip()
    if len(t) < 2:
        return False
    if re.search(r'^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$', t) or '-->' in t:
        return False
    lower_t = t.lower()
    junk = {
        '00:00', '0:00', 'webvtt', '[music]', '[applause]', '[silence]',
        '[blank_audio]', 'subtitle by', 'thank you for watching',
        'thanks for watching', 'you', 'none'
    }
    if lower_t in junk:
        return False
    cleaned = re.sub(r'[\d:\.\-\s\>\[\]\(\)]', '', t)
    if not cleaned:
        return False
    return True


@login_required
def generate_insights(request, room_code):
    from django.conf import settings as django_settings

    meeting = get_object_or_404(Meeting, room_code=room_code)

    # Allow host or any participant who attended/joined the meeting
    is_participant = (
        meeting.host == request.user or
        meeting.participants.filter(user=request.user).exists()
    )
    if not is_participant:
        return HttpResponseForbidden("You must be a participant of this meeting to generate insights.")

    # Concurrency / Duplicate generation deduplication (10s window)
    try:
        existing_insight = getattr(meeting, 'ai_insight', None)
        if existing_insight and existing_insight.summary and request.GET.get('force') != '1':
            age_seconds = (timezone.now() - existing_insight.created_at).total_seconds()
            if age_seconds < 10:
                messages.info(request, "Displaying current AI insights.")
                return redirect('meeting_details', room_code=room_code)
    except Exception:
        pass

    all_transcripts = TranscriptMessage.objects.filter(
        meeting=meeting
    ).order_by('timestamp').select_related('speaker')

    transcripts = [t for t in all_transcripts if is_valid_speech_text(t.text)]

    if not transcripts:
        messages.warning(
            request,
            "No transcript found for this meeting. Speak during the meeting with microphone enabled — your speech is recorded automatically."
        )
        return redirect('meeting_details', room_code=room_code)

    # Build canonical transcript text with speaker attribution, timestamp, and detected language
    transcript_lines = [
        f"[{t.timestamp.strftime('%H:%M')}] {t.speaker.username} ({t.detected_language or t.language or 'auto'}): {t.text}"
        for t in transcripts
    ]
    full_transcript = "\n".join(transcript_lines)
    output_language = request.GET.get('lang', 'English')

    prompt = f"""You are an expert AI meeting intelligence and minutes writer.

Analyze the following canonical meeting transcript with speaker attributions and timestamps.
Note that participants may have spoken in different languages (e.g. English, Tamil, Hindi, Malayalam, Telugu, etc.).

STRICT ACCURACY RULES:
- Do NOT hallucinate or fabricate information, decisions, action items, or deadlines that were not explicitly stated.
- Preserve speaker context and attribution accurately.
- Extract ONLY the actual points discussed, actual decisions agreed upon, and real action items.
- If no decisions or action items were discussed, write "None identified".
- Write the final meeting summary and minutes clearly in {output_language}.

OUTPUT FORMAT (Follow this exact structure with clear markdown headings and bullet points):
**Executive Summary:**
[A concise 2-3 sentence overview of the meeting purpose, key topics, and outcomes]

**Key Discussion Points:**
• [Topic/point discussed and who spoke about it]
• [Topic/point discussed]

**Decisions & Conclusions:**
• [Explicit decision or conclusion agreed upon during the meeting]

**Action Items:**
• [Task description] — Assigned to: [Person's Name] | Deadline: [Date/Time if mentioned, else 'Not specified']

**Important Dates or Deadlines:**
• [Date/deadline mentioned]

MEETING TRANSCRIPT:
{full_transcript}

MEETING MINUTES:"""

    summary = None
    api_key = getattr(django_settings, 'GEMINI_API_KEY', '') or os.getenv('GEMINI_API_KEY', '')

    if api_key and api_key.strip():
        for model_name in ['gemini-3.6-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.7-flash', 'gemini-flash-latest']:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key.strip()}"
                headers = {
                    'Content-Type': 'application/json'
                }
                payload = {
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {
                        'temperature': 0.1,
                        'maxOutputTokens': 1200,
                    }
                }
                r = requests.post(url, headers=headers, json=payload, timeout=20)
                if r.status_code == 200:
                    res = r.json()
                    candidates = res.get('candidates', [])
                    if candidates:
                        parts = candidates[0].get('content', {}).get('parts', [])
                        if parts and parts[0].get('text'):
                            summary = parts[0]['text'].strip()
                            if summary:
                                break
            except Exception as e:
                continue

    # Fallback summary if Gemini unavailable or rate-limited
    if not summary:
        unique_speakers = list(set(t.speaker.username for t in transcripts))
        word_count = sum(len(t.text.split()) for t in transcripts)
        first_ts = transcripts[0].timestamp
        last_ts = transcripts[-1].timestamp
        duration_mins = max(1, int((last_ts - first_ts).total_seconds() // 60))

        # Discussion points grouped by speaker
        discussion_by_speaker = []
        for spk in unique_speakers:
            spk_texts = [t.text for t in transcripts if t.speaker.username == spk]
            if spk_texts:
                sample_pts = spk_texts[:3]
                for pt in sample_pts:
                    discussion_by_speaker.append(f"• **{spk}**: {pt}")

        # Extract decisions
        decision_keywords = ['agree', 'agreed', 'decide', 'decided', 'finalize', 'finalized', 'concluded', 'approved', 'resolved', 'confirmed']
        decisions_found = [
            f"• {t.speaker.username}: {t.text}" for t in transcripts
            if any(kw in t.text.lower() for kw in decision_keywords)
        ]

        # Extract action items
        action_keywords = ['need to', 'must', 'will', 'action', 'task', 'todo', 'should', 'complete', 'assigned', 'handle', 'fix', 'finish']
        actions_found = [
            f"• {t.text} — Assigned to: {t.speaker.username}" for t in transcripts
            if any(kw in t.text.lower() for kw in action_keywords)
        ]

        # Extract deadlines / dates
        time_keywords = ['tomorrow', 'today', 'pm', 'am', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'deadline', 'schedule', 'by ']
        deadlines_found = [
            f"• {t.text}" for t in transcripts
            if any(kw in t.text.lower() for kw in time_keywords)
        ]

        disc_str = "\n".join(discussion_by_speaker[:6]) if discussion_by_speaker else "• General discussion held across agenda items."
        dec_str = "\n".join(decisions_found[:4]) if decisions_found else "• General consensus reached on discussed topics."
        act_str = "\n".join(actions_found[:5]) if actions_found else "• Follow-up items as noted in discussion transcript."
        dl_str = "\n".join(deadlines_found[:3]) if deadlines_found else "• Key deliverables to proceed according to project schedule."

        summary = f"""**Executive Summary:**
Meeting held with {len(unique_speakers)} participant(s) ({', '.join(unique_speakers)}) discussing core agenda topics over {duration_mins} minute(s) across {len(transcripts)} dialogue entries ({word_count} words).

**Key Discussion Points:**
{disc_str}

**Decisions & Conclusions:**
{dec_str}

**Action Items:**
{act_str}

**Important Dates or Deadlines:**
{dl_str}"""

    # Speaker word stats
    word_counts = Counter()
    for t in transcripts:
        word_counts[t.speaker.username] += len(t.text.split())

    total_words = sum(word_counts.values()) or 1
    speaker_stats = {
        speaker: {
            'words': count,
            'percentage': round((count / total_words) * 100, 1)
        }
        for speaker, count in word_counts.most_common()
    }

    full_text = " ".join(t.text for t in transcripts)
    sentiment_score = analyze_sentiment(full_text)

    action_sentences = [
        t.text for t in transcripts
        if any(kw in t.text.lower() for kw in ['need to', 'must', 'action', 'task', 'will', 'todo', 'should', 'complete', 'assigned'])
    ]
    action_items_text = "\n".join(f"• {s}" for s in action_sentences) if action_sentences else "Please refer to the Meeting Minutes above."

    insight, _ = AIInsight.objects.get_or_create(meeting=meeting)
    insight.summary = summary
    insight.speaker_stats = speaker_stats
    insight.created_at_language = output_language
    insight.action_items = action_items_text
    insight.sentiment_score = sentiment_score
    insight.save()

    messages.success(request, "Meeting minutes and AI insights generated successfully!")
    return redirect('meeting_details', room_code=room_code)
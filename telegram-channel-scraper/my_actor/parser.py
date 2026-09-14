"""HTML parsing for Telegram public channel preview pages (https://t.me/s/<channel>)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag

_COUNT_RE = re.compile(r'^\s*([\d.,]+)\s*([KMB]?)\s*$', re.IGNORECASE)
_BG_IMAGE_RE = re.compile(r"background-image:\s*url\(['\"]?([^'\")]+)['\"]?\)")
_MULTIPLIERS = {'': 1, 'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}


def parse_count(value: str | None) -> int | None:
    """Turn Telegram's abbreviated counters ("18.8M", "9.55K", "1 234") into integers."""
    if not value:
        return None
    match = _COUNT_RE.match(value.replace('\xa0', ' '))
    if not match:
        digits = re.sub(r'[^\d]', '', value)
        return int(digits) if digits else None
    number, suffix = match.groups()
    try:
        return int(round(float(number.replace(',', '').replace(' ', '')) * _MULTIPLIERS[suffix.upper()]))
    except ValueError:
        return None


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    value = node.get_text(' ', strip=True)
    return value or None


def _bg_image(node: Tag | None) -> str | None:
    if node is None:
        return None
    style = node.get('style') or ''
    match = _BG_IMAGE_RE.search(style)
    return match.group(1) if match else None


def _first_attr(node: Tag | None, attr: str) -> str | None:
    if node is None:
        return None
    value = node.get(attr)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parse_channel_info(soup: BeautifulSoup, username: str) -> dict[str, Any] | None:
    """Extract the channel header block. Returns None when the page is not a channel preview."""
    info = soup.select_one('.tgme_channel_info')
    if info is None:
        return None

    counters: dict[str, int | None] = {}
    for counter in info.select('.tgme_channel_info_counter'):
        kind = _text(counter.select_one('.counter_type'))
        value = _text(counter.select_one('.counter_value'))
        if kind:
            counters[kind.lower()] = parse_count(value)

    header_username = _text(info.select_one('.tgme_channel_info_header_username'))
    return {
        'type': 'channel',
        'channel': username,
        'url': f'https://t.me/{username}',
        'title': _text(info.select_one('.tgme_channel_info_header_title')),
        'username': (header_username or '').lstrip('@') or username,
        'description': _text(info.select_one('.tgme_channel_info_description')),
        'descriptionHtml': str(info.select_one('.tgme_channel_info_description') or '') or None,
        'photoUrl': _first_attr(info.select_one('.tgme_page_photo_image img'), 'src'),
        'subscribers': counters.get('subscribers') or counters.get('subscriber'),
        'photosCount': counters.get('photos') or counters.get('photo'),
        'videosCount': counters.get('videos') or counters.get('video'),
        'filesCount': counters.get('files') or counters.get('file'),
        'linksCount': counters.get('links') or counters.get('link'),
        'isVerified': info.select_one('.tgme_channel_info_header_labels .verified-icon') is not None,
    }


def parse_posts(soup: BeautifulSoup, username: str, *, include_html: bool = False) -> list[dict[str, Any]]:
    """Extract every post on a preview page, newest last (same order as the page)."""
    posts: list[dict[str, Any]] = []
    for wrap in soup.select('.tgme_widget_message_wrap'):
        message = wrap.select_one('.tgme_widget_message')
        if message is None:
            continue
        post = _parse_message(message, username, include_html=include_html)
        if post is not None:
            posts.append(post)
    return posts


def _parse_message(message: Tag, username: str, *, include_html: bool) -> dict[str, Any] | None:
    data_post = _first_attr(message, 'data-post') or ''
    if '/' not in data_post:
        return None
    channel_from_page, _, post_id_raw = data_post.rpartition('/')
    try:
        post_id = int(post_id_raw)
    except ValueError:
        return None

    classes = message.get('class') or []
    is_service = 'service_message' in classes or message.select_one('.tgme_widget_message_service') is not None

    time_node = message.select_one('.tgme_widget_message_date time')
    date_iso = _first_attr(time_node, 'datetime')
    timestamp = None
    if date_iso:
        try:
            timestamp = int(datetime.fromisoformat(date_iso.replace('Z', '+00:00')).astimezone(timezone.utc).timestamp())
        except ValueError:
            timestamp = None

    text_node = message.select_one('.tgme_widget_message_text')
    text = None
    if text_node is not None:
        for br in text_node.find_all('br'):
            br.replace_with('\n')
        text = text_node.get_text('', strip=False).strip() or None

    links = []
    seen_links: set[str] = set()
    if text_node is not None:
        for anchor in text_node.find_all('a', href=True):
            href = anchor['href']
            if href.startswith('#') or href in seen_links:
                continue
            seen_links.add(href)
            links.append({'url': href, 'text': anchor.get_text(' ', strip=True) or None})

    image_urls = [url for url in (_bg_image(node) for node in message.select('.tgme_widget_message_photo_wrap')) if url]

    videos = []
    for player in message.select('.tgme_widget_message_video_player, .tgme_widget_message_roundvideo_player'):
        video_tag = player.select_one('video')
        videos.append({
            'url': _first_attr(video_tag, 'src'),
            'thumbnailUrl': _bg_image(player.select_one('.tgme_widget_message_video_thumb, .tgme_widget_message_roundvideo_thumb')),
            'duration': _text(player.select_one('.message_video_duration, .message_media_duration')),
            'isRoundVideo': 'tgme_widget_message_roundvideo_player' in (player.get('class') or []),
        })

    documents = []
    for doc in message.select('.tgme_widget_message_document_wrap'):
        documents.append({
            'title': _text(doc.select_one('.tgme_widget_message_document_title')),
            'extra': _text(doc.select_one('.tgme_widget_message_document_extra')),
        })

    voice = message.select_one('.tgme_widget_message_voice_player')
    audio = None
    if voice is not None:
        audio = {
            'url': _first_attr(voice.select_one('audio'), 'src'),
            'duration': _text(voice.select_one('.tgme_widget_message_voice_duration')),
        }

    sticker = message.select_one('.tgme_widget_message_sticker_wrap')
    sticker_url = _bg_image(sticker.select_one('.tgme_widget_message_sticker')) if sticker else None

    poll = message.select_one('.tgme_widget_message_poll')
    poll_data = None
    if poll is not None:
        poll_data = {
            'question': _text(poll.select_one('.tgme_widget_message_poll_question')),
            'type': _text(poll.select_one('.tgme_widget_message_poll_type')),
            'options': [
                {
                    'text': _text(opt.select_one('.tgme_widget_message_poll_option_text')),
                    'percent': _text(opt.select_one('.tgme_widget_message_poll_option_percent')),
                }
                for opt in poll.select('.tgme_widget_message_poll_option')
            ],
            'votes': _text(poll.select_one('.tgme_widget_message_poll_votes')),
        }

    preview_node = message.select_one('.tgme_widget_message_link_preview')
    link_preview = None
    if preview_node is not None:
        link_preview = {
            'url': _first_attr(preview_node, 'href'),
            'siteName': _text(preview_node.select_one('.link_preview_site_name')),
            'title': _text(preview_node.select_one('.link_preview_title')),
            'description': _text(preview_node.select_one('.link_preview_description')),
            'imageUrl': _bg_image(preview_node.select_one('.link_preview_image, .link_preview_right_image')),
        }

    forwarded_node = message.select_one('.tgme_widget_message_forwarded_from_name')
    forwarded_from = None
    if forwarded_node is not None:
        forwarded_from = {
            'name': _text(forwarded_node),
            'url': _first_attr(forwarded_node, 'href'),
        }

    reply_node = message.select_one('.tgme_widget_message_reply')
    reply_to = None
    if reply_node is not None:
        reply_to = {
            'url': _first_attr(reply_node, 'href'),
            'author': _text(reply_node.select_one('.tgme_widget_message_author_name')),
            'text': _text(reply_node.select_one('.tgme_widget_message_text')),
        }

    reactions = []
    reactions_total = 0
    for reaction in message.select('.tgme_widget_message_reactions .tgme_reaction'):
        count = parse_count(reaction.get_text(' ', strip=True)) or 0
        emoji_node = reaction.select_one('.emoji b, tg-emoji')
        emoji = None
        if emoji_node is not None:
            emoji = emoji_node.get_text(strip=True) or None
        custom_emoji_id = _first_attr(reaction.select_one('tg-emoji'), 'emoji-id')
        is_paid = 'tgme_reaction_paid' in (reaction.get('class') or [])
        reactions.append({
            'emoji': '⭐' if is_paid and not emoji else emoji,
            'customEmojiId': custom_emoji_id,
            'isPaid': is_paid,
            'count': count,
        })
        reactions_total += count

    views_raw = _text(message.select_one('.tgme_widget_message_views'))
    author = _text(message.select_one('.tgme_widget_message_from_author'))
    edited = message.select_one('.tgme_widget_message_meta .tgme_widget_message_date .time_edited') is not None

    post: dict[str, Any] = {
        'type': 'post',
        'channel': channel_from_page or username,
        'postId': post_id,
        'url': f'https://t.me/{channel_from_page or username}/{post_id}',
        'date': date_iso,
        'timestamp': timestamp,
        'text': text,
        'author': author,
        'views': parse_count(views_raw),
        'viewsRaw': views_raw,
        'reactions': reactions,
        'reactionsTotal': reactions_total,
        'links': links,
        'linkPreview': link_preview,
        'imageUrls': image_urls,
        'videoUrls': [v['url'] for v in videos if v.get('url')],
        'videos': videos,
        'documents': documents,
        'audio': audio,
        'stickerUrl': sticker_url,
        'poll': poll_data,
        'forwardedFrom': forwarded_from,
        'replyTo': reply_to,
        'isForwarded': forwarded_from is not None,
        'isReply': reply_to is not None,
        'isEdited': edited,
        'isServiceMessage': is_service,
        'hasMedia': bool(image_urls or videos or documents or audio or sticker_url),
    }
    if include_html and text_node is not None:
        post['textHtml'] = str(text_node)
    return post


def min_post_id(posts: list[dict[str, Any]]) -> int | None:
    ids = [p['postId'] for p in posts if isinstance(p.get('postId'), int)]
    return min(ids) if ids else None

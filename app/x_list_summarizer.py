import asyncio
import re
import json
import os
import sys
import html
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from twikit import Client
import httpx
import base64
from urllib.parse import urlparse

class XListFetcher:
    """Class to fetch and process tweets from X lists with premium reporting."""
    
    def __init__(self, cookies_path='browser_session/cookies.json', list_owner=None):
        self.client = Client('en-US')
        self.cookies_path = Path(cookies_path)
        self.list_owner_pref = list_owner
        self.list_info = {
            'name': 'X List Summary', 'owner': list_owner or 'Unknown', 
            'owner_name': list_owner or 'Unknown', 'member_count': 0,
            'profile_image_url': None
        }
        self.list_url = ""
        
    async def login(self):
        """Load cookies and verify login."""
        if not self.cookies_path.exists():
            return False, "Cookies file not found. Please login via settings."
        
        try:
            self.client.load_cookies(str(self.cookies_path))
            user = await self.client.user()
            return True, f"Logged in as @{user.screen_name}"
        except Exception as e:
            return False, f"Login failed: {e}"

    async def verify_session(self):
        """Lightweight check to see if current session is still valid."""
        if not self.cookies_path.exists(): return False, "No cookies"
        try:
            self.client.load_cookies(str(self.cookies_path))
            # Just fetch own user info
            user = await self.client.user()
            return True, f"OK (@{user.screen_name})"
        except Exception as e:
            err = str(e)
            if '401' in err: return False, "Expired/Unauthorized (401)"
            if 'rate limit' in err.lower(): return False, "Rate Limited by X"
            return False, f"Invalid: {err[:30]}"

    def extract_list_id(self, url_or_id: str) -> str:
        """Extract numeric list ID from URL."""
        if url_or_id.isdigit():
            return url_or_id
        match = re.search(r'/lists/(\d+)', url_or_id)
        return match.group(1) if match else url_or_id

    def extract_owner_from_url(self, url: str) -> str:
        """Extract username from list URL."""
        match = re.search(r'x\.com/([^/]+)/lists/', url)
        if not match:
            match = re.search(r'twitter\.com/([^/]+)/lists/', url)
        return match.group(1) if match else None

    async def _resolve_list_redirect(self, list_id: str) -> str:
        """Find list owner via redirect logic."""
        url = f"https://x.com/i/lists/{list_id}"
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, follow_redirects=False, timeout=5)
                if resp.status_code in [301, 302]:
                    loc = resp.headers.get('location', '')
                    return self.extract_owner_from_url(loc)
            except:
                pass
        return None

    async def fetch_list_tweets(self, list_url_or_id: str, max_tweets: int = 100):
        """Fetch tweets from a list (Aggregates metadata)."""
        list_id = self.extract_list_id(list_url_or_id)
        print(f"📋 Fetching list {list_id}...")
        
        tweets = []
        cursor = None
        current_url = list_url_or_id if list_url_or_id.startswith('http') else f"https://x.com/i/lists/{list_id}"
        
        try:
            # 1. Get List Info
            try:
                l_info = await self.client.get_list(list_id)
                # If we have a generic name, use the first real list name we find
                if self.list_info['name'] == 'X List Summary':
                    self.list_info['name'] = getattr(l_info, 'name', 'Unknown List')
                
                # Accumulate member counts
                self.list_info['member_count'] += getattr(l_info, 'member_count', 0)
                
                # Resolve owner details if not set or if we need to fetch profile for preference
                owner_obj = getattr(l_info, 'user', getattr(l_info, 'creator', None))
                
                # If we have a preference, try to fetch its profile if we haven't already
                if self.list_owner_pref and not self.list_info['profile_image_url']:
                    try:
                        u_info = await self.client.get_user_by_screen_name(self.list_owner_pref)
                        self.list_info['owner'] = self.list_owner_pref
                        self.list_info['owner_name'] = getattr(u_info, 'name', self.list_owner_pref)
                        self.list_info['profile_image_url'] = getattr(u_info, 'profile_image_url', None)
                    except: pass
                
                # If we still have no owner info, use what the list provides
                if not self.list_info['profile_image_url'] and owner_obj:
                    self.list_info['owner'] = getattr(owner_obj, 'screen_name', self.list_info['owner'])
                    self.list_info['owner_name'] = getattr(owner_obj, 'name', self.list_info['owner'])
                    self.list_info['profile_image_url'] = getattr(owner_obj, 'profile_image_url', None)
            except:
                pass

            # 2. Fetch Tweets
            while len(tweets) < max_tweets:
                batch = await self.client.get_list_tweets(list_id, count=min(40, max_tweets - len(tweets)), cursor=cursor)
                if not batch: break
                
                for tweet in batch:
                    # Resolve Links & Entities (Filtering out twimg.com)
                    resolved_links = set()
                    url_map = {}
                    display_map = {}
                    
                    # Access entities from _legacy dict (where twikit stores tweet metadata)
                    legacy = getattr(tweet, '_legacy', {}) or {}
                    entities = legacy.get('entities', {}) or getattr(tweet, 'entities', {}) or {}
                    urls = entities.get('urls', []) if isinstance(entities, dict) else []
                    for u in urls:
                        short = u.get('url')
                        expanded = u.get('expanded_url')
                        if short:
                            url_map[short] = expanded or short
                            display_map[short] = u.get('display_url', expanded or short)
                    
                    for expanded in url_map.values():
                        if not any(d in expanded.lower() for d in ['x.com', 'twitter.com', 'twimg.com']):
                            resolved_links.add(expanded)
                    
                    # Media extraction (with best bitrate video)
                    media = []
                    seen_media_ids = set()
                    
                    def extract_media(t_obj):
                        if not t_obj: return
                        leg = getattr(t_obj, '_legacy', {})
                        ext_ents = leg.get('extended_entities', {}) or leg.get('entities', {}) or {}
                        for m in ext_ents.get('media', []):
                            m_id = m.get('id_str')
                            if m_id in seen_media_ids: continue
                            m_type = m.get('type')
                            m_url = m.get('media_url_https')
                            
                            if m_type == 'photo':
                                media.append({'type': 'photo', 'url': m_url, 'id': m_id})
                            elif m_type in ['video', 'animated_gif']:
                                variants = m.get('video_info', {}).get('variants', [])
                                best = sorted([v for v in variants if v.get('content_type') == 'video/mp4'], 
                                            key=lambda x: x.get('bitrate', 0), reverse=True)
                                if best:
                                    media.append({'type': m_type, 'url': best[0]['url'], 'thumbnail': m_url, 'id': m_id})
                            seen_media_ids.add(m_id)

                    extract_media(tweet)
                    if hasattr(tweet, 'retweeted_status'): extract_media(tweet.retweeted_status)
                    if hasattr(tweet, 'quoted_status'): extract_media(tweet.quoted_status)
                    
                    # Card Extraction (Link Previews)
                    tweet_card = None
                    def extract_card(t_obj):
                        if not t_obj: return None
                        c = getattr(t_obj, 'card', None)
                        if not c: return None
                        
                        try:
                            # Twikit card object processing
                            bv = getattr(c, 'binding_values', {})
                            if not bv: return None
                            
                            res = {}
                            if 'title' in bv: res['title'] = bv['title'].get('string_value')
                            if 'description' in bv: res['description'] = bv['description'].get('string_value')
                            if 'thumbnail_image' in bv: 
                                res['image'] = bv['thumbnail_image'].get('image_value', {}).get('url')
                            elif 'player_image' in bv:
                                res['image'] = bv['player_image'].get('image_value', {}).get('url')
                                
                            if res.get('title'): return res
                        except: pass
                        return None

                    tweet_card = extract_card(tweet)
                    if not tweet_card and hasattr(tweet, 'retweeted_status'): 
                        tweet_card = extract_card(tweet.retweeted_status)
                    if not tweet_card and hasattr(tweet, 'quoted_status'):
                        tweet_card = extract_card(tweet.quoted_status)
                    
                    # Clean Text
                    clean_text = tweet.text
                    for short, expanded in url_map.items():
                        if short in clean_text:
                            # Use expanded for external, display for internal
                            tgt = expanded if not any(d in expanded.lower() for d in ['x.com', 'twitter.com', 'twimg.com']) else display_map.get(short, short)
                            clean_text = clean_text.replace(short, tgt)

                    tweets.append({
                        'id': tweet.id, 'text': clean_text, 'author': tweet.user.screen_name,
                        'links': list(resolved_links), 'media': media, 'card': tweet_card,
                        'likes': getattr(tweet, 'favorite_count', 0),
                        'retweets': getattr(tweet, 'retweet_count', 0),
                        'replies': getattr(tweet, 'reply_count', 0),
                        'quotes': getattr(tweet, 'quote_count', 0),
                        'bookmarks': getattr(tweet, 'bookmark_count', 0)
                    })
                
                cursor = batch.next_cursor
                if not cursor: break
            
            return tweets
        except Exception as e:
            print(f"❌ Error: {e}")
            return tweets

    def aggregate_by_links(self, tweets: list) -> dict:
        """Group tweets by link and sort by engagement."""
        by_link = defaultdict(list)
        no_links = []
        for t in tweets:
            if t.get('links'):
                for link in t['links']: by_link[link].append(t)
            else: no_links.append(t)
        
        sorted_links = sorted(by_link.items(), key=lambda x: sum(
            t['likes'] + (t['retweets'] * 1.5) + (t['replies'] * 2.0) + t['quotes'] + t['bookmarks'] for t in x[1]
        ), reverse=True)
        
        return {'by_link': sorted_links, 'no_links': no_links}

    def _build_card_html(self, tweet_data):
        """Build HTML for a link preview card inside a tweet."""
        card = tweet_data.get('card')
        if not card: return ""
        
        # Determine target URL from links if not in card
        url = tweet_data['links'][0] if tweet_data.get('links') else "#"
        
        img_html = f'<div class="tc-img"><img src="{card["image"]}" loading="lazy"></div>' if card.get('image') else ""
        desc_html = f'<div class="tc-desc">{card["description"]}</div>' if card.get('description') else ""
        
        return f'''
        <a href="{url}" target="_blank" rel="noopener" class="tweet-card-link">
            <div class="tc-container">
                {img_html}
                <div class="tc-content">
                    <div class="tc-title">{card["title"]}</div>
                    {desc_html}
                    <div class="tc-site">{self._extract_domain(url)}</div>
                </div>
            </div>
        </a>'''

    def _build_media_html(self, tweet):
        """Build HTML for images and videos in a tweet."""
        media = tweet.get('media', [])
        if not media: return ""
        
        html_parts = ['<div class="tweet-media">']
        for m in media:
            if m['type'] == 'photo':
                html_parts.append(f'<div class="media-item"><img src="{m["url"]}" loading="lazy"></div>')
            elif m['type'] == 'animated_gif':
                # GIFs on X are actually videos, they should loop and be muted
                html_parts.append(f'''
                <div class="media-item">
                    <video playsinline autoplay loop muted poster="{m.get("thumbnail")}">
                        <source src="{m["url"]}" type="video/mp4">
                    </video>
                </div>''')
            elif m['type'] == 'video':
                html_parts.append(f'''
                <div class="media-item">
                    <video controls playsinline poster="{m.get("thumbnail")}">
                        <source src="{m["url"]}" type="video/mp4">
                    </video>
                </div>''')
        html_parts.append('</div>')
        return "".join(html_parts)

    def _extract_domain(self, url):
        """Extract domain from URL."""
        try:
            domain = urlparse(url).netloc
            return domain.replace('www.', '')
        except:
            return ""

    def _build_link_card(self, url):
        """Build a link card component with a high-quality favicon."""
        domain = self._extract_domain(url)
        
        # YouTube Special Case
        if 'youtube.com' in domain or 'youtu.be' in domain:
            y_id = url.split('v=')[-1].split('&')[0] if 'v=' in url else url.split('/')[-1]
            return f'''
            <div class="l-card">
                <div class="l-dom">YOUTUBE</div>
                <div class="v-con">
                    <iframe src="https://www.youtube.com/embed/{y_id}" allowfullscreen></iframe>
                </div>
            </div>'''
            
        # Standard Link Card with Favicon
        favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
        
        return f'''
        <div class="shared-content">
            <div class="link-card">
                <a href="{url}" target="_blank" rel="noopener" class="link-icon-wrap">
                    <img src="{favicon_url}" class="link-icon-img" onerror="this.src='https://abs.twimg.com/responsive-web/client-web/icon-ios.b1fdcd7a.png'">
                </a>
                <div class="link-details">
                    <div class="link-domain">{domain}</div>
                    <a href="{url}" target="_blank" rel="noopener" class="link-url-text">{url}</a>
                </div>
            </div>
        </div>'''

    def generate_html_report(self, aggregated, ai_summary, output_path, tweet_count=0):
        """Premium HTML Report generator matching the new design."""
        timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        link_count = len(aggregated['by_link'])
        
        # Get Logo as Base64 to ensure it works in iframes/external
        logo_uri = "icon.png" # Fallback
        try:
            icon_path = Path("icon.png")
            if icon_path.exists():
                with open(icon_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode()
                    logo_uri = f"data:image/png;base64,{encoded_string}"
        except: pass

        # Build List Identity Section
        owner_info = f"by {self.list_info['owner_name'] or 'Unknown'} (@{self.list_info['owner']}) • {self.list_info['member_count']} members total"
        
        # Build Content Blocks
        grouped_html = ""
        for link, tweets in aggregated['by_link'][:30]:
            tweet_list = ""
            for t in tweets[:5]:
                tweet_url = f"https://x.com/{t['author']}/status/{t['id']}"
                media_html = self._build_media_html(t)
                card_html = self._build_card_html(t)
                tweet_list += f'''
                <div class="tweet">
                    <div class="tweet-header">
                        <a href="{tweet_url}" target="_blank" rel="noopener" class="author">@{t['author']} ↗️</a>
                        <div class="tweet-meta">
                            <span class="metrics">
                                ❤️ {t['likes']} | 
                                🔄 {t['retweets']} | 
                                💬 {t['replies']} | 
                                🔁 {t['quotes']} | 
                                🔖 {t['bookmarks']}
                            </span>
                            <a href="{tweet_url}" target="_blank" rel="noopener" class="view-tweet">View Tweet</a>
                        </div>
                    </div>
                    <p class="tweet-text">{t['text']}</p>
                    {media_html}
                    {card_html}
                </div>'''
            
            grouped_html += f'''
            <div class="link-group">
                <div class="link-group-header">
                    <span class="tweet-count">Shared by {len(tweets)} tweets</span>
                </div>
                
                {tweet_list}
                {self._build_link_card(link)}
            </div>'''

        individual_html = ""
        for t in aggregated['no_links'][:30]:
            tweet_url = f"https://x.com/{t['author']}/status/{t['id']}"
            individual_html += f'''
            <div class="tweet">
                <div class="tweet-header">
                    <a href="{tweet_url}" target="_blank" rel="noopener" class="author">@{t['author']} ↗️</a>
                    <div class="tweet-meta">
                        <span class="metrics">
                            ❤️ {t['likes']} | 
                            🔄 {t['retweets']} | 
                            💬 {t['replies']} | 
                            🔁 {t['quotes']} | 
                            🔖 {t['bookmarks']}
                        </span>
                        <a href="{tweet_url}" target="_blank" rel="noopener" class="view-tweet">View Tweet</a>
                    </div>
                </div>
                <p class="tweet-text">{t['text']}</p>
                {self._build_media_html(t)}
                {self._build_card_html(t)}
            </div>'''

        report_html = self._get_report_template().format(
            title=self.list_info['name'],
            owner_line=owner_info,
            tweet_count=tweet_count,
            link_count=link_count,
            timestamp=timestamp,
            logo_uri=logo_uri,
            summary=self._md_to_html(ai_summary),
            grouped=grouped_html,
            individual=individual_html,
            profile_img=self.list_info.get('profile_image_url') or 'https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png'
        )
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_html)

    def _md_to_html(self, text):
        """Intelligent parser for AI summary to create tables/headers correctly matching screenshot."""
        if not text: return ""
        lines = text.strip().split('\n')
        html_out = []
        in_table = False
        current_section = "general"
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # 1. Handle Headers (detect section shift)
            if line.startswith('#'):
                if in_table:
                    html_out.append('</table></div>')
                    in_table = False
                
                header_text = line.lstrip('#').strip().lower()
                clean_header = line.lstrip('#').strip()
                
                if 'tl;dr' in header_text:
                    current_section = "tldr"
                elif 'topics' in header_text or 'themes' in header_text:
                    current_section = "themes"
                elif 'shared content' in header_text or 'mentions' in header_text:
                    current_section = "shared"
                else:
                    current_section = "general"
                    
                html_out.append(f'<h3 class="sum-h">{clean_header}</h3>')
                continue

            # 2. Handle List Items / Table Rows
            is_item = line.startswith('- ') or line.startswith('* ') or (line[0].isdigit() and line[1] == '.')
            
            # Parsing logic based on section
            if current_section == "tldr" and (' :: ' in line or ' : ' in line or ' - ' in line):
                if not in_table:
                    html_out.append('<div class="table-con"><table><tr class="t-head"><th>What\'s being talked about</th><th>Why it matters / shared</th></tr>')
                    in_table = True
                parts = line.split(' :: ', 1) if ' :: ' in line else (line.split(' : ', 1) if ' : ' in line else line.split(' - ', 1))
                name = parts[0].lstrip('- *123456789. ').replace('X/Twitter List - ', '').strip()
                val = parts[1].strip()
                html_out.append(f'<tr><td class="t-name">{name}</td><td>{val}</td></tr>')
            
            elif current_section == "shared" and (' :: ' in line or (line.count(' : ') >= 2)):
                if not in_table:
                    html_out.append('<div class="table-con"><table><tr class="t-head"><th>Content</th><th># of mentions</th><th>Why it\'s trending</th></tr>')
                    in_table = True
                
                parts = line.split(' :: ') if ' :: ' in line else line.split(' : ')
                if len(parts) >= 3:
                    name = parts[0].lstrip('- *123456789. ').strip()
                    count = parts[1].strip()
                    reason = " : ".join(parts[2:]).strip()
                    html_out.append(f'<tr><td class="t-name">{name}</td><td>{count}</td><td>{reason}</td></tr>')
                elif len(parts) == 2:
                    name = parts[0].lstrip('- *123456789. ').strip()
                    val = parts[1].strip()
                    html_out.append(f'<tr><td class="t-name">{name}</td><td>{val}</td></tr>')

            elif is_item:
                if in_table:
                    html_out.append('</table></div>')
                    in_table = False
                content = line.lstrip('- *123456789. ').strip()
                # Clean up redundant markdown bolding like **Theme**
                content = content.replace('**', '')
                
                if ' – ' in line or ' - ' in content or ': ' in content:
                    sep = ' – ' if ' – ' in line else (' - ' if ' - ' in content else ': ')
                    p = content.split(sep, 1)
                    if len(p) == 2:
                        html_out.append(f'<p><strong>{p[0]}</strong> – {p[1]}</p>')
                    else:
                        html_out.append(f'<p>• {content}</p>')
                else:
                    html_out.append(f'<p>• {content}</p>')
            else:
                if in_table:
                    html_out.append('</table></div>')
                    in_table = False
                html_out.append(f'<p>{line}</p>')

        if in_table: html_out.append('</table></div>')
        return "".join(html_out)

    def _get_report_template(self):
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Report - {title}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{ 
            --bg: #0b0e14; --card: #151921; --border: #232a35; --text: #eff3f4; 
            --dim: #949ba4; --accent: #1d9bf0; --accent-dim: #1d9bf015;
            --purple-grad: linear-gradient(135deg, #a855f7 0%, #1d9bf0 100%);
        }}
        body {{ font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 40px 20px; line-height: 1.6; }}
        .con {{ max-width: 1000px; margin: 0 auto; }}
        
        /* Top Header Area */
        .page-header {{ text-align: center; margin-bottom: 60px; }}
        .main-logo {{ width: 100px; height: 100px; border-radius: 20px; margin-bottom: 30px; box-shadow: 0 0 40px rgba(29, 155, 240, 0.2); }}
        .main-title {{ font-size: 48px; font-weight: 800; margin: 0; background: var(--purple-grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -1px; }}
        .gen-date {{ color: var(--dim); font-size: 14px; margin-top: 10px; font-weight: 500; }}

        /* List Identity Card */
        .list-card {{ 
            background: var(--card); border: 1px solid var(--border); border-radius: 20px; 
            padding: 24px; display: flex; align-items: center; gap: 20px; margin-bottom: 40px;
        }}
        .l-img {{ width: 56px; height: 56px; border-radius: 50%; border: 2px solid var(--border); }}
        .l-title {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; display: block; }}
        .l-meta {{ color: var(--dim); font-size: 13px; }}

        /* Stats Row */
        .stats-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 60px; }}
        .stat-box {{ 
            background: #11151c; border: 1px solid var(--border); border-radius: 24px; 
            padding: 40px; text-align: center; transition: 0.3s;
        }}
        .stat-val {{ font-size: 48px; font-weight: 800; color: var(--accent); display: block; margin-bottom: 8px; }}
        .stat-lbl {{ font-size: 12px; font-weight: 800; color: var(--dim); letter-spacing: 2px; text-transform: uppercase; }}

        /* AI Summary Section */
        .sum-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 32px; padding: 48px; margin-bottom: 60px; }}
        .sum-title {{ font-size: 24px; font-weight: 800; margin-bottom: 25px; display: flex; align-items: center; gap: 15px; color: var(--accent); }}
        .sum-intro {{ color: var(--dim); font-size: 15px; margin-bottom: 40px; line-height: 1.7; }}
        .sum-h {{ font-size: 18px; font-weight: 800; margin: 40px 0 20px; border-top: 1px solid var(--border); padding-top: 30px; }}
        
        .table-con {{ background: #0b0e14; border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }}
        th {{ background: #1a202a; padding: 16px 24px; color: #1d9bf0; font-weight: 700; font-size: 13px; }}
        td {{ padding: 20px 24px; border-bottom: 1px solid var(--border); vertical-align: top; }}
        .t-name {{ font-weight: 700; width: 200px; color: var(--text); }}
        
        /* Link Card Components */
        .link-group {{ background: var(--card); border: 1px solid var(--border); border-radius: 24px; margin-bottom: 32px; overflow: hidden; }}
        .link-group-header {{ padding: 16px 24px; border-bottom: 1px solid var(--border); background: #1a202a; }}
        .tweet-count {{ color: #1d9bf0; font-weight: 700; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
        .shared-content {{ padding: 0 24px 24px 24px; }}
        .link-card {{ 
            background: #0b0e14; border: 1px solid var(--border); border-radius: 16px; 
            padding: 16px; display: flex; gap: 16px; align-items: center; margin-top: 10px;
        }}
        
        .link-icon-wrap {{ 
            width: 54px; height: 54px; display: flex; align-items: center; justify-content: center; 
            border-radius: 14px; border: 1px solid var(--border); background: #151921; 
            transition: 0.2s; overflow: hidden; flex-shrink: 0;
        }}
        .link-icon-wrap:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
        .link-icon-img {{ width: 32px; height: 32px; object-fit: contain; }}
        .link-details {{ display: flex; flex-direction: column; overflow: hidden; }}
        .link-domain {{ color: var(--dim); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }}
        .link-url-text {{ color: var(--accent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-decoration: none; font-size: 14px; font-weight: 500; }}
        .link-url-text:hover {{ text-decoration: underline; }}

        .tweet {{ padding: 24px; border-bottom: 1px solid var(--border); }}
        .tweet:last-child {{ border-bottom: none; }}
        .tweet-header {{ display: flex; justify-content: space-between; margin-bottom: 12px; align-items: flex-start; }}
        .author {{ font-weight: 800; color: var(--text); text-decoration: none; font-size: 15px; }}
        .tweet-meta {{ display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }}
        .metrics {{ color: var(--dim); font-size: 12px; font-weight: 600; }}
        .view-tweet {{ color: var(--accent); font-size: 12px; text-decoration: none; font-weight: 600; margin-top: 2px; }}
        .view-tweet:hover {{ text-decoration: underline; }}
        
        .tweet-text {{ margin: 0; white-space: pre-wrap; font-size: 15px; color: #eff3f4; line-height: 1.5; }}
        
        .tweet-media {{ margin-top: 15px; border-radius: 16px; overflow: hidden; border: 1px solid var(--border); display: grid; gap: 2px; }}
        .tweet-media-grid {{ grid-template-columns: 1fr 1fr; }}
        .media-item img, .media-item video {{ width: 100%; display: block; object-fit: cover; max-height: 550px; }}
        
        .l-card {{ background: #000; padding: 24px; margin: 24px; border-radius: 16px; border: 1px solid var(--border); }}
        .l-dom {{ color: var(--dim); font-size: 11px; font-weight: 800; text-transform: uppercase; margin-bottom: 6px; letter-spacing: 1px; }}
        .v-con {{ position: relative; padding-bottom: 56.25%; height: 0; background: #000; }}
        .v-con iframe {{ position: absolute; width: 100%; height: 100%; border:0; }}
        
        .link-url-text:hover {{ text-decoration: underline; }}

        /* Tweet Card (Link Preview) Styles */
        .tweet-card-link {{ text-decoration: none; color: inherit; display: block; margin-top: 12px; }}
        .tc-container {{ 
            border: 1px solid var(--border); border-radius: 16px; overflow: hidden; 
            background: #0b0e14; transition: 0.2s;
        }}
        .tc-container:hover {{ border-color: var(--accent); background: #11151d; }}
        .tc-img img {{ width: 100%; aspect-ratio: 1.91 / 1; object-fit: cover; border-bottom: 1px solid var(--border); }}
        .tc-content {{ padding: 12px 16px; }}
        .tc-title {{ font-weight: 700; font-size: 15px; margin-bottom: 4px; color: var(--text); }}
        .tc-desc {{ color: var(--dim); font-size: 13px; line-height: 1.4; margin-bottom: 8px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
        .tc-site {{ font-size: 11px; text-transform: uppercase; color: var(--dim); letter-spacing: 0.5px; }}

        footer {{ text-align: center; color: var(--dim); font-size: 13px; margin-top: 100px; padding: 40px; border-top: 1px solid var(--border); }}
    </style>
</head>
<body>
    <div class="con">
        <div class="page-header">
            <img src="{logo_uri}" class="main-logo" onerror="this.src='https://abs.twimg.com/responsive-web/client-web/icon-ios.b1fdcd7a.png'">
            <h1 class="main-title">X List Summary</h1>
            <div class="gen-date">Generated on {timestamp}</div>
        </div>

        <div class="list-card">
            <img src="{profile_img}" class="l-img">
            <div>
                <span class="l-title">{title}</span>
                <span class="l-meta">{owner_line}</span>
            </div>
        </div>

        <div class="stats-row">
            <div class="stat-box">
                <span class="stat-val">{tweet_count}</span>
                <span class="stat-lbl">Tweets</span>
            </div>
            <div class="stat-box">
                <span class="stat-val">{link_count}</span>
                <span class="stat-lbl">Shared Links</span>
            </div>
        </div>

        <div class="sum-card">
            <div class="sum-title">🤖 AI Global Summary</div>
            <p class="sum-intro">Consolidated insights generated by analyzing the aggregate content of all fetched tweets using your selected AI model. Focuses on primary themes, breaking news, and consensus opinions.</p>
            {summary}
        </div>

        <h2 class="sec-label">📈 Top Shared Links</h2>
        {grouped}

        <h2 class="sec-label">💬 Other Relevant Tweets</h2>
        <div class="link-group">{individual}</div>

        <footer>
            Generated by X List Summarizer Premium • {timestamp}<br>
            All data fetched directly from official X API via browser session.
        </footer>
    </div>
</body>
</html>'''

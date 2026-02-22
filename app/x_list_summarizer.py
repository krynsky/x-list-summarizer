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
            'name': 'X List Summary', 'list_names': [],
            'owner': list_owner or 'Unknown', 
            'owner_name': list_owner or 'Unknown', 'member_count': 0,
            'profile_image_url': None
        }
        self.list_url = ""
        self.cache_dir = Path('cache')
        self.user_cache_path = self.cache_dir / 'user_ids.json'
        self.user_cache = self._load_user_cache()
        
    def _load_user_cache(self):
        """Load username -> User ID mapping from local cache."""
        if not self.user_cache_path.exists():
            return {}
        try:
            with open(self.user_cache_path, 'r') as f:
                return json.load(f)
        except:
            return {}

    def _save_user_cache(self):
        """Save username -> User ID mapping to local cache."""
        self.cache_dir.mkdir(exist_ok=True)
        try:
            with open(self.user_cache_path, 'w') as f:
                json.dump(self.user_cache, f)
        except:
            pass

    async def get_user_id(self, username: str) -> str:
        """Get User ID for a username, using cache if available to save API requests."""
        username = username.lower().replace('@', '').strip()
        if username in self.user_cache:
            return self.user_cache[username]
            
        try:
            user = await self.client.get_user_by_screen_name(username)
            self.user_cache[username] = user.id
            self._save_user_cache()
            return user.id
        except Exception as e:
            if '429' in str(e) or 'rate limit' in str(e).lower():
                raise Exception("X Rate Limit reached. Please wait 15 minutes before searching new users.")
            raise e
        
    async def login(self):
        """Load cookies and verify login."""
        if not self.cookies_path.exists():
            return False, "Cookies file not found. Please login via settings."
        
        try:
            self.client.load_cookies(str(self.cookies_path))
            user = await self.client.user()
            return True, f"Logged in as @{user.screen_name}"
        except Exception as e:
            err = str(e)
            # 401 = definitively expired/invalid — hard fail
            if '401' in err:
                return False, f"Session expired (401). Please re-import your cookies."
            # 429 = rate limited during the session check itself — hard fail
            if '429' in err or 'rate limit' in err.lower():
                return False, f"Rate limited (429) during session check."
            # 404 = X's verify endpoint is temporarily unavailable (known X flakiness).
            # Cookies are still loaded; proceed optimistically and let the tweet
            # fetch surface a real auth error if the session is actually invalid.
            if '404' in err:
                print(f"⚠️ [Login] 404 from X session check (transient flakiness) — proceeding with loaded cookies.")
                return True, "Session loaded (X returned 404 verifying user, proceeding anyway)"
            # Any other unexpected error — hard fail with detail
            return False, f"Login failed: {err}"

    async def verify_session(self, retries=1):
        """Lightweight check to see if current session is still valid with retry for transient errors."""
        if not self.cookies_path.exists(): return False, "No cookies"
        
        last_err = ""
        for attempt in range(retries + 1):
            try:
                self.client.load_cookies(str(self.cookies_path))
                # Just fetch own user info
                user = await self.client.user()
                return True, f"OK (@{user.screen_name})"
            except Exception as e:
                last_err = str(e)
                # If it's a 401, don't retry, it's definitive
                if '401' in last_err: break
                # If it's a rate limit or 404, wait a tiny bit and retry
                if attempt < retries:
                    await asyncio.sleep(1)
                    continue
        
        err = last_err
        if '401' in err: return False, "Expired/Unauthorized (401)"
        if 'rate limit' in err.lower() or '429' in err: return False, "Rate Limited by X"
        if '404' in err: return False, "X Service Busy (404)"
        return False, f"Invalid: {err[:30]}"

    async def get_user_memberships(self, username: str):
        """Fetch all lists that a specific user is a member of (Profiler feature)."""
        try:
            self.client.load_cookies(str(self.cookies_path))
            user_id = await self.get_user_id(username)
            
            memberships = []
            cursor = '-1' # v1.1 cursor starts at -1
            
            # Fetch memberships (lists the user is in)
            # twikit 2.3.3 lacks get_user_memberships, so we use manual v1.1 call
            while True:
                url = f'https://api.twitter.com/1.1/lists/memberships.json?user_id={user_id}&count=50'
                if cursor and cursor != '-1':
                    url += f'&cursor={cursor}'
                
                # We MUST pass client._base_headers for authentication
                # client.get() handles the transaction IDs automatically
                response, raw_response = await self.client.get(url, headers=self.client._base_headers)
                
                if not response or 'lists' not in response:
                    break
                
                for l in response['lists']:
                    name = l.get('name', '')
                    if name:
                        owner = l.get('user', {}).get('screen_name', 'Unknown')
                        list_id = l.get('id_str', '')
                        memberships.append({
                            'name': name,
                            'owner': owner,
                            'id': list_id
                        })
                
                cursor = str(response.get('next_cursor_str', '0'))
                if cursor == '0' or not cursor or len(memberships) > 500: # Safety cap
                    break
                    
            print(f"✅ Found {len(memberships)} memberships for {username}")
            return memberships
        except Exception as e:
            print(f"❌ Error fetching memberships for {username}: {e}")
            return []

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

    async def fetch_list_tweets(self, list_url_or_id: str, max_tweets: int = 100, delay: float = 0):
        """Fetch tweets from a list (Aggregates metadata)."""
        if delay > 0:
            await asyncio.sleep(delay)
            
        list_id = self.extract_list_id(list_url_or_id)
        print(f"📋 Fetching list {list_id}...")
        
        tweets = []
        cursor = None
        current_url = list_url_or_id if list_url_or_id.startswith('http') else f"https://x.com/i/lists/{list_id}"
        
        try:
            # 1. Get List Info
            list_name = None
            member_count_for_list = 0
            owner_obj = None

            try:
                l_info = await self.client.get_list(list_id)
                list_name = getattr(l_info, 'name', None)
                member_count_for_list = getattr(l_info, 'member_count', 0)
                owner_obj = getattr(l_info, 'user', getattr(l_info, 'creator', None))
                print(f"✅ List info via twikit: '{list_name}', {member_count_for_list} members")
            except Exception as _e:
                print(f"⚠️ get_list() failed for {list_id}: {_e} — trying v1.1 fallback")
                try:
                    v1_url = f'https://api.twitter.com/1.1/lists/show.json?list_id={list_id}'
                    v1_resp, _ = await self.client.get(v1_url, headers=self.client._base_headers)
                    if v1_resp:
                        list_name = v1_resp.get('name')
                        member_count_for_list = v1_resp.get('member_count', 0)
                        print(f"✅ List info via v1.1: '{list_name}', {member_count_for_list} members")
                except Exception as _e2:
                    print(f"⚠️ v1.1 list info also failed for {list_id}: {_e2}")

            if list_name:
                self.list_info['list_names'].append(list_name)
                if self.list_info['name'] == 'X List Summary':
                    self.list_info['name'] = list_name
            self.list_info['member_count'] += member_count_for_list

            # Resolve owner profile (independent of list name)
            if self.list_owner_pref and not self.list_info['profile_image_url']:
                try:
                    u_info = await self.client.get_user_by_screen_name(self.list_owner_pref)
                    self.list_info['owner'] = self.list_owner_pref
                    self.list_info['owner_name'] = getattr(u_info, 'name', self.list_owner_pref)
                    self.list_info['profile_image_url'] = getattr(u_info, 'profile_image_url', None)
                except: pass

            if not self.list_info['profile_image_url'] and owner_obj:
                self.list_info['owner'] = getattr(owner_obj, 'screen_name', self.list_info['owner'])
                self.list_info['owner_name'] = getattr(owner_obj, 'name', self.list_info['owner'])
                self.list_info['profile_image_url'] = getattr(owner_obj, 'profile_image_url', None)

            # 2. Fetch Tweets
            while len(tweets) < max_tweets:
                batch = await self.client.get_list_tweets(list_id, count=min(40, max_tweets - len(tweets)), cursor=cursor)
                if not batch: break
                
                for tweet in batch:
                    # Resolve Links & Entities — including retweets and quote tweets
                    resolved_links = set()
                    url_map = {}
                    display_map = {}

                    def extract_urls_from(t_obj):
                        if not t_obj: return
                        leg = getattr(t_obj, '_legacy', {}) or {}
                        ents = leg.get('entities', {}) or getattr(t_obj, 'entities', {}) or {}
                        if isinstance(ents, dict):
                            for u in ents.get('urls', []):
                                short = u.get('url')
                                expanded = u.get('expanded_url')
                                if short:
                                    url_map[short] = expanded or short
                                    display_map[short] = u.get('display_url', expanded or short)

                    extract_urls_from(tweet)
                    extract_urls_from(getattr(tweet, 'retweeted_tweet', None))
                    extract_urls_from(getattr(tweet, 'quote', None))

                    for expanded in url_map.values():
                        if not any(d in expanded.lower() for d in ['x.com', 'twitter.com', 'twimg.com', 't.co']):
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
                    if hasattr(tweet, 'retweeted_tweet'): extract_media(tweet.retweeted_tweet)
                    if hasattr(tweet, 'quote'): extract_media(tweet.quote)
                    
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
            err = str(e)
            # Re-raise rate limit and auth errors so the caller can show a clear message
            if '429' in err or 'rate limit' in err.lower():
                raise Exception(f"X Rate Limit reached fetching list {list_id}. Please wait 15 minutes and try again.") from e
            if '401' in err or 'unauthorized' in err.lower():
                raise Exception(f"X session unauthorized (401) fetching list {list_id}. Please re-import your cookies via Settings → X Account.") from e
            # Non-fatal errors (e.g. intermittent network): log and return whatever we got
            print(f"❌ Error fetching list {list_id}: {err}")
            return tweets

    def aggregate_by_links(self, tweets: list) -> dict:
        """Group tweets by link and sort by engagement."""
        by_link = defaultdict(list)
        no_links = []
        for t in tweets:
            if t.get('links'):
                for link in t['links']:
                    # Skip unresolved t.co short URLs
                    if 't.co/' in link.lower():
                        continue
                    by_link[link].append(t)
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

    def _build_media_html(self, tweet, seen_urls=None):
        """Build HTML for images and videos in a tweet. seen_urls deduplicates within a group."""
        media = tweet.get('media', [])
        if not media: return ""

        html_parts = []
        for m in media:
            # Use thumbnail URL as the dedup key (stable CDN image, same across retweets)
            url_key = m.get('thumbnail') or m.get('url')
            if seen_urls is not None:
                if url_key in seen_urls:
                    continue  # duplicate — already shown in this group
                seen_urls.add(url_key)

            if m['type'] == 'photo':
                html_parts.append(f'<div class="media-item"><img src="{m["url"]}" loading="lazy"></div>')
            elif m['type'] == 'animated_gif':
                html_parts.append(f'''
                <div class="media-item">
                    <video playsinline autoplay loop muted poster="{m.get("thumbnail")}">
                        <source src="{m["url"]}" type="video/mp4">
                    </video>
                </div>''')
            elif m['type'] == 'video':
                # X video CDN requires session auth — show poster thumbnail with play overlay
                tweet_url = f"https://x.com/{tweet.get('author', 'i')}/status/{tweet.get('id', '')}"
                thumb = m.get('thumbnail', '')
                if thumb:
                    html_parts.append(f'''
                    <div class="media-item">
                        <a href="{tweet_url}" target="_blank" rel="noopener" class="video-thumb-link" title="Watch on X">
                            <img src="{thumb}" loading="lazy">
                            <div class="play-overlay">&#9654;</div>
                        </a>
                    </div>''')

        if not html_parts: return ""
        return '<div class="tweet-media">' + "".join(html_parts) + '</div>'

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

    def _parse_ai_insights(self, ai_summary):
        """Parse AI output (domain :: why lines) into {domain: why} dict."""
        insights = {}
        if not ai_summary:
            return insights
        for line in ai_summary.strip().split('\n'):
            line = line.strip()
            if ' :: ' in line:
                parts = line.split(' :: ', 1)
                domain = parts[0].strip().lower().lstrip('0123456789. -*#[]')
                why = parts[1].strip()
                if domain and why:
                    insights[domain] = why
                    # Also index by base domain (e.g. cloudflare.com → blog.cloudflare.com)
                    base = '.'.join(domain.split('.')[-2:]) if domain.count('.') >= 2 else domain
                    if base not in insights:
                        insights[base] = why
        return insights

    def generate_html_report(self, aggregated, ai_summary, output_path, tweet_count=0, ai_model=''):
        """Standardized HTML Report generator."""
        timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        link_count = len(aggregated['by_link'])
        
        # Logo as Base64
        logo_uri = "icon.png"
        try:
            icon_path = Path("icon.png")
            if icon_path.exists():
                with open(icon_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode()
                    logo_uri = f"data:image/png;base64,{encoded_string}"
        except: pass

        # Build display title from all fetched list names
        list_names = self.list_info.get('list_names', [])
        if not list_names:
            list_names = [self.list_info.get('name', 'X List Summary')]
        display_title = ' &amp; '.join(list_names)

        owner_info = f"by {self.list_info['owner_name'] or 'Unknown'} (@{self.list_info['owner']}) &bull; {self.list_info['member_count']:,} members total"
        
        # Parse AI insights into {domain: why} lookup
        insights = self._parse_ai_insights(ai_summary)
        
        # Build "Most Shared Content & Why" table with inline expandable tweet rows
        table_rows = ""
        for i, (link, tweets) in enumerate(aggregated['by_link'][:20]):
            domain = self._extract_domain(link)
            why = insights.get(domain, '')
            if not why:
                base = '.'.join(domain.split('.')[-2:]) if domain.count('.') >= 2 else domain
                why = insights.get(base, '&mdash;')
            count = len(tweets)
            label = str(count)
            favicon = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"

            # Build the tweet list for this link's expand row (dedup media across retweets)
            tweet_list = ""
            group_seen_media = set()
            for t in tweets[:5]:
                tweet_url = f"https://x.com/{t['author']}/status/{t['id']}"
                media_html = self._build_media_html(t, seen_urls=group_seen_media)
                card_html = self._build_card_html(t)
                tweet_list += f'''
                    <div class="tweet">
                        <div class="tweet-header">
                            <a href="{tweet_url}" target="_blank" rel="noopener" class="author">@{t['author']} &#8599;</a>
                            <div class="tweet-meta">
                                <span class="metrics">&#10084;&#65039; {t['likes']} | &#128260; {t['retweets']} | &#128172; {t['replies']} | &#128279; {t['bookmarks']}</span>
                                <a href="{tweet_url}" target="_blank" rel="noopener" class="view-tweet">View Tweet</a>
                            </div>
                        </div>
                        <p class="tweet-text">{t['text']}</p>
                        {media_html}
                        {card_html}
                    </div>'''

            table_rows += f'''
                <tr class="insight-row" id="insight-row-{i}">
                    <td class="t-name">
                        <div class="t-domain-wrap">
                            <img src="{favicon}" class="t-fav" onerror="this.style.display='none'">
                            <a href="{link}" target="_blank" rel="noopener" class="t-link">{domain}</a>
                        </div>
                    </td>
                    <td class="t-count-cell">
                        <a class="tweet-expand-link" data-idx="{i}" onclick="toggleRow({i}); return false;">{label} <span class="expand-arrow" id="arrow-{i}">&#9660;</span></a>
                    </td>
                    <td class="t-why">{why}</td>
                </tr>
                <tr class="tweet-expand-row" id="tweets-{i}">
                    <td colspan="3" class="tweet-expand-cell">
                        {tweet_list}
                        {self._build_link_card(link)}
                    </td>
                </tr>'''

        insights_html = f'''
        <div class="insights-card">
            <div class="insights-title">&#128293; Most Shared Content &amp; Why</div>
            <div class="table-con">
                <table>
                    <tr class="t-head">
                        <th>Content</th>
                        <th>Mentions</th>
                        <th>Why It&rsquo;s Trending</th>
                    </tr>
                    {table_rows}
                </table>
            </div>
        </div>'''

        # Build individual tweets section
        individual_html = ""
        for t in aggregated['no_links'][:30]:
            tweet_url = f"https://x.com/{t['author']}/status/{t['id']}"
            individual_html += f'''
            <div class="tweet">
                <div class="tweet-header">
                    <a href="{tweet_url}" target="_blank" rel="noopener" class="author">@{t['author']} &#8599;</a>
                    <div class="tweet-meta">
                        <span class="metrics">&#10084;&#65039; {t['likes']} | &#128260; {t['retweets']} | &#128172; {t['replies']} | &#128279; {t['bookmarks']}</span>
                        <a href="{tweet_url}" target="_blank" rel="noopener" class="view-tweet">View Tweet</a>
                    </div>
                </div>
                <p class="tweet-text">{t['text']}</p>
                {self._build_media_html(t)}
                {self._build_card_html(t)}
            </div>'''

        ai_model_html = f'<div class="gen-model">&#129302; AI Analysis by {ai_model}</div>' if ai_model else ''

        report_html = self._get_report_template().format(
            title=display_title,
            owner_line=owner_info,
            tweet_count=tweet_count,
            link_count=link_count,
            timestamp=timestamp,
            ai_model_html=ai_model_html,
            logo_uri=logo_uri,
            insights=insights_html,
            individual=individual_html,
            profile_img=self.list_info.get('profile_image_url') or 'https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png'
        )
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_html)

    def _md_to_html(self, text):
        """Kept for backward compatibility — no longer called in report generation."""
        return ""

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
            --dim: #949ba4; --accent: #1d9bf0; --green: #00ba7c;
            --purple-grad: linear-gradient(135deg, #a855f7 0%, #1d9bf0 100%);
            --fire-grad: linear-gradient(135deg, #f97316 0%, #ef4444 50%, #a855f7 100%);
        }}
        * {{ box-sizing: border-box; }}
        body {{ font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 40px 20px; line-height: 1.6; }}
        .con {{ max-width: 1000px; margin: 0 auto; }}

        /* Header */
        .page-header {{ text-align: center; margin-bottom: 60px; }}
        .main-logo {{ width: 100px; height: 100px; border-radius: 20px; margin-bottom: 30px; box-shadow: 0 0 40px rgba(29,155,240,0.2); }}
        .main-title {{ font-size: 48px; font-weight: 800; margin: 0; background: var(--purple-grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -1px; }}
        .gen-date {{ color: var(--dim); font-size: 14px; margin-top: 10px; font-weight: 500; }}
        .gen-model {{ color: var(--dim); font-size: 12px; margin-top: 6px; font-weight: 500; opacity: 0.7; }}

        /* List Card */
        .list-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 24px; display: flex; align-items: center; gap: 20px; margin-bottom: 40px; }}
        .l-img {{ width: 56px; height: 56px; border-radius: 50%; border: 2px solid var(--border); }}
        .l-title {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; display: block; }}
        .l-meta {{ color: var(--dim); font-size: 13px; }}

        /* Stats */
        .stats-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 40px; }}
        .stat-box {{ background: #11151c; border: 1px solid var(--border); border-radius: 24px; padding: 40px; text-align: center; }}
        .stat-val {{ font-size: 48px; font-weight: 800; color: var(--accent); display: block; margin-bottom: 8px; }}
        .stat-lbl {{ font-size: 12px; font-weight: 800; color: var(--dim); letter-spacing: 2px; text-transform: uppercase; }}

        /* Insights Card (Most Shared Content & Why) */
        .insights-card {{
            background: linear-gradient(135deg, rgba(249,115,22,0.06) 0%, rgba(239,68,68,0.06) 50%, rgba(168,85,247,0.06) 100%);
            border: 1px solid rgba(249,115,22,0.35);
            border-radius: 32px; padding: 40px; margin-bottom: 60px;
            box-shadow: 0 0 40px rgba(249,115,22,0.06);
        }}
        .insights-title {{ font-size: 24px; font-weight: 800; margin-bottom: 25px; background: var(--fire-grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}

        /* Table */
        .table-con {{ background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }}
        th {{ background: rgba(26,32,42,0.8); padding: 14px 20px; color: var(--accent); font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
        td {{ padding: 16px 20px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background: rgba(29,155,240,0.03); }}
        .t-name {{ width: 220px; }}
        .t-domain-wrap {{ display: flex; align-items: center; gap: 10px; }}
        .t-fav {{ width: 20px; height: 20px; border-radius: 4px; flex-shrink: 0; }}
        .t-link {{ color: var(--text); font-weight: 700; text-decoration: none; font-size: 13px; }}
        .t-link:hover {{ color: var(--accent); }}
        .t-count-cell {{ width: 130px; white-space: nowrap; }}
        .tweet-expand-link {{
            display: inline-flex; align-items: center; gap: 6px;
            color: var(--accent); font-weight: 700; font-size: 13px; text-decoration: none;
            background: rgba(29,155,240,0.1); border: 1px solid rgba(29,155,240,0.25);
            border-radius: 20px; padding: 5px 12px; transition: 0.2s; cursor: pointer;
        }}
        .tweet-expand-link:hover {{ background: rgba(29,155,240,0.2); border-color: var(--accent); }}
        .t-why {{ color: var(--dim); font-size: 13px; line-height: 1.5; }}

        /* Section Labels */
        .sec-label {{ font-size: 20px; font-weight: 800; margin: 0 0 20px; color: var(--text); display: flex; align-items: center; gap: 10px; }}

        /* Inline tweet expand rows */
        .tweet-expand-row {{ display: none; }}
        .tweet-expand-row.open {{ display: table-row; }}
        .tweet-expand-cell {{
            padding: 0 !important; border-top: 2px solid rgba(29,155,240,0.2);
            background: rgba(0,0,0,0.25);
        }}
        .expand-arrow {{ display: inline-block; transition: transform 0.2s; font-style: normal; }}
        .tweet-expand-link.open .expand-arrow {{ transform: rotate(180deg); }}
        .insight-row.open td {{ background: rgba(29,155,240,0.04); }}

        /* Tweets */
        .tweet {{ padding: 22px 24px; border-bottom: 1px solid var(--border); }}
        .tweet:last-of-type {{ border-bottom: none; }}
        .tweet-header {{ display: flex; justify-content: space-between; margin-bottom: 10px; align-items: flex-start; gap: 12px; }}
        .author {{ font-weight: 800; color: var(--text); text-decoration: none; font-size: 15px; white-space: nowrap; }}
        .author:hover {{ color: var(--accent); }}
        .tweet-meta {{ display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; }}
        .metrics {{ color: var(--dim); font-size: 12px; font-weight: 500; white-space: nowrap; }}
        .view-tweet {{ color: var(--accent); font-size: 12px; text-decoration: none; font-weight: 600; }}
        .view-tweet:hover {{ text-decoration: underline; }}
        .tweet-text {{ margin: 0; white-space: pre-wrap; font-size: 14px; color: var(--text); line-height: 1.55; }}

        /* Media */
        .tweet-media {{ margin-top: 12px; border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }}
        .media-item img, .media-item video {{ width: 100%; display: block; object-fit: cover; max-height: 450px; }}

        /* Link card */
        .shared-content {{ padding: 0 24px 20px 24px; }}
        .link-card {{ background: #0b0e14; border: 1px solid var(--border); border-radius: 14px; padding: 14px; display: flex; gap: 14px; align-items: center; margin-top: 8px; }}
        .link-icon-wrap {{ width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; border-radius: 12px; border: 1px solid var(--border); background: #151921; transition: 0.2s; overflow: hidden; flex-shrink: 0; }}
        .link-icon-wrap:hover {{ border-color: var(--accent); }}
        .link-icon-img {{ width: 28px; height: 28px; object-fit: contain; }}
        .link-details {{ display: flex; flex-direction: column; overflow: hidden; }}
        .link-domain {{ color: var(--dim); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }}
        .link-url-text {{ color: var(--accent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-decoration: none; font-size: 13px; font-weight: 500; }}
        .link-url-text:hover {{ text-decoration: underline; }}
        .l-card {{ background: #000; padding: 20px; margin: 20px; border-radius: 14px; border: 1px solid var(--border); }}
        .l-dom {{ color: var(--dim); font-size: 11px; font-weight: 800; text-transform: uppercase; margin-bottom: 6px; letter-spacing: 1px; }}

        /* Video thumbnail with play overlay */
        .video-thumb-link {{ position: relative; display: block; }}
        .video-thumb-link img {{ width: 100%; border-radius: 8px; display: block; }}
        .play-overlay {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 52px; height: 52px; background: rgba(0,0,0,0.65); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 22px; color: #fff; transition: background 0.2s; pointer-events: none; }}
        .video-thumb-link:hover .play-overlay {{ background: rgba(29,155,240,0.85); }}
        .v-con {{ position: relative; padding-bottom: 56.25%; height: 0; background: #000; }}
        .v-con iframe {{ position: absolute; width: 100%; height: 100%; border: 0; }}

        /* Tweet card preview */
        .tweet-card-link {{ text-decoration: none; color: inherit; display: block; margin-top: 10px; }}
        .tc-container {{ border: 1px solid var(--border); border-radius: 14px; overflow: hidden; background: #0b0e14; transition: 0.2s; }}
        .tc-container:hover {{ border-color: var(--accent); }}
        .tc-img img {{ width: 100%; aspect-ratio: 1.91/1; object-fit: cover; border-bottom: 1px solid var(--border); }}
        .tc-content {{ padding: 10px 14px; }}
        .tc-title {{ font-weight: 700; font-size: 14px; margin-bottom: 4px; }}
        .tc-desc {{ color: var(--dim); font-size: 12px; line-height: 1.4; margin-bottom: 6px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
        .tc-site {{ font-size: 11px; text-transform: uppercase; color: var(--dim); letter-spacing: 0.5px; }}

        /* Other tweets wrapper */
        .no-links-group {{ background: var(--card); border: 1px solid var(--border); border-radius: 20px; overflow: hidden; }}

        footer {{ text-align: center; color: var(--dim); font-size: 13px; margin-top: 100px; padding: 40px; border-top: 1px solid var(--border); }}
    </style>
</head>
<body>
    <div class="con">
        <div class="page-header">
            <img src="{logo_uri}" class="main-logo" onerror="this.src='https://abs.twimg.com/responsive-web/client-web/icon-ios.b1fdcd7a.png'">
            <h1 class="main-title">X List Summary</h1>
            <div class="gen-date">Generated on {timestamp}</div>
            {ai_model_html}
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
                <span class="stat-lbl">Tweets Analyzed</span>
            </div>
            <div class="stat-box">
                <span class="stat-val">{link_count}</span>
                <span class="stat-lbl">Shared Links</span>
            </div>
        </div>

        {insights}

        <h2 class="sec-label">&#128172; Other Relevant Tweets</h2>
        <div class="no-links-group">{individual}</div>

        <footer>
            Generated by X List Summarizer &bull; {timestamp}<br>
            All data fetched directly from official X API via browser session.
        </footer>
    </div>

    <script>
        function toggleRow(idx) {{
            var row = document.getElementById('tweets-' + idx);
            var link = document.querySelector('.tweet-expand-link[data-idx="' + idx + '"]');
            var insightRow = document.getElementById('insight-row-' + idx);
            if (!row) return;
            var isOpen = row.classList.contains('open');
            row.classList.toggle('open', !isOpen);
            if (link) link.classList.toggle('open', !isOpen);
            if (insightRow) insightRow.classList.toggle('open', !isOpen);
        }}
    </script>
</body>
</html>'''

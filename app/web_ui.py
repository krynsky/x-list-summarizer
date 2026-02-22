import http.server
from http.server import ThreadingHTTPServer
import socketserver
import json
import os
import sys
import threading
import time
import webbrowser
import asyncio
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime

# Add app to path for imports
sys.path.append(str(Path(__file__).parent))
from x_list_summarizer import XListFetcher
from llm_providers import LLMProvider

PORT = 8765
CONFIG_PATH = Path('config.json')
COOKIES_PATH = Path('browser_session/cookies.json')
OUTPUT_DIR = Path('output')

class DashHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.app_state = kwargs.pop('app_state', {})
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # Suppress terminal spam
        return

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_root(self):
        html = self.get_reconstructed_html().encode('utf-8')
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        return html

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            self._send_root()
        else:
            super().do_HEAD()

    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/':
            html = self._send_root()
            self.wfile.write(html)
            return
            
        elif parsed.path == '/api/status':
            now = time.time()
            limit_ok = 30 # 30 seconds for healthy status
            limit_err = 5  # Only 5 seconds for error/invalid status
            
            # 1. X Auth Verification (Cached)
            last_x = getattr(DashHandler, '_x_cache', None)
            last_x_time = getattr(DashHandler, '_x_cache_time', 0)
            
            x_aged = (now - last_x_time)
            x_limit = limit_ok if (last_x and last_x.get('active')) else limit_err
            
            if not last_x or x_aged > x_limit:
                x_status = {'active': False, 'message': 'Not logged in'}
                if COOKIES_PATH.exists():
                    try:
                        fetcher = XListFetcher()
                        loop = asyncio.new_event_loop()
                        success, msg = loop.run_until_complete(fetcher.verify_session(retries=2))
                        x_status = {'active': success, 'message': msg}
                        loop.close()
                    except Exception as e: x_status = {'active': False, 'message': 'Auth Error'}
                DashHandler._x_cache = x_status
                DashHandler._x_cache_time = now
            else: x_status = DashHandler._x_cache
            
            # 2. AI Verification (Cached)
            last_ai = getattr(DashHandler, '_ai_cache', None)
            last_ai_time = getattr(DashHandler, '_ai_cache_time', 0)
            
            ai_aged = (now - last_ai_time)
            ai_limit = limit_ok if (last_ai and last_ai.get('active')) else limit_err

            if not last_ai or ai_aged > ai_limit:
                ai_status = {'active': False, 'message': 'Checking...'}
                try:
                    config = self.load_config()
                    provider = LLMProvider(config)
                    ai_status = provider.verify()
                    DashHandler._ai_cache = ai_status
                    DashHandler._ai_cache_time = now
                except: ai_status = {'active': False, 'message': 'Error'}
            else: ai_status = DashHandler._ai_cache

            self.send_json({
                'running': self.app_state.get('running', False),
                'status_msg': self.app_state.get('status_msg', 'Ready'),
                'progress': self.app_state.get('progress', 0),
                'error': self.app_state.get('error'),
                'x_auth': x_status,
                'ai_status': ai_status,
                'last_report': self.app_state.get('last_report'),
                'output_path': str(OUTPUT_DIR.resolve())
            })

        elif parsed.path == '/api/config':
            self.send_json(self.load_config())

        elif parsed.path == '/api/history':
            history = []
            metadata = {}
            meta_path = OUTPUT_DIR / 'history.json'
            if meta_path.exists():
                try:
                    with open(meta_path, 'r') as f: metadata = json.load(f)
                except: pass

            if OUTPUT_DIR.exists():
                files = sorted(OUTPUT_DIR.glob('summary_*.html'), key=os.path.getmtime, reverse=True)
                for f in files:
                    file_meta = metadata.get(f.name, {})
                    history.append({
                        'filename': f.name,
                        'date': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'size': f.stat().st_size,
                        'name': file_meta.get('name', 'Analysis Report'),
                        'username': file_meta.get('username', 'Unknown'),
                        'tweets': file_meta.get('tweets', 0),
                        'links': file_meta.get('links', 0),
                        'profile_img': file_meta.get('profile_img', 'https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png'),
                        'members': file_meta.get('members', 0)
                    })
            self.send_json(history)
            
        elif parsed.path.startswith('/output/'):
            filename = parsed.path.split('/')[-1]
            if filename == 'latest':
                files = sorted(OUTPUT_DIR.glob('summary_*.html'), key=os.path.getmtime, reverse=True)
                if files: filename = files[0].name
                else: self.send_error(404); return
                
            file_path = OUTPUT_DIR / filename
            if file_path.exists():
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
        
        elif parsed.path == '/api/open-folder':
            try:
                import subprocess
                out_abs = str(OUTPUT_DIR.absolute())
                if sys.platform == 'win32':
                    subprocess.run(['explorer', out_abs])
                else:
                    cmd = ['open', out_abs] if sys.platform == 'darwin' else ['xdg-open', out_abs]
                    subprocess.run(cmd)
                self.send_json({'success': True})
            except Exception as e:
                self.send_json({'success': False, 'error': str(e)})

        elif parsed.path == '/api/reset-progress':
            self.app_state['progress'] = 0
            self.app_state['status_msg'] = 'Ready'
            self.app_state['last_report'] = None
            self.send_json({'success': True})
            
        else:
            super().do_GET()

    def _analyze_word_frequencies(self, memberships):
        import re
        from collections import Counter
        
        stop_words = {
            'the', 'and', 'for', 'with', 'your', 'from', 'this', 'that', 'list', 'lists', 'member',
            'of', 'to', 'in', 'on', 'at', 'by', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'but', 'if', 'or', 'because', 'as', 'until', 'while',
            'about', 'into', 'through', 'during', 'before', 'after', 'above', 'below', 'up', 'down', 'out',
            'off', 'over', 'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
            'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such',
            'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'can', 'will',
            'just', 'should', 'now', 'my', 'me', 'our', 'i', 'a', 'it', 'its'
        }
        
        words = []
        for l in memberships:
            name = l.get('name', '')
            cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', name.lower())
            tokens = cleaned.split()
            for t in tokens:
                if len(t) > 2 and t not in stop_words:
                    words.append(t)
        return dict(Counter(words).most_common(100))

    def do_POST(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/api/profile':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            params = json.loads(post_data)
            username = params.get('username', '').strip().replace('@', '')
            
            if not username:
                self.send_json({'success': False, 'error': 'Username required'})
                return
            
            try:
                fetcher = XListFetcher()
                # Run async membership fetching in a synchronous context
                memberships = asyncio.run(fetcher.get_user_memberships(username))
                word_counts = self._analyze_word_frequencies(memberships)
                
                self.send_json({
                    'success': True,
                    'username': username,
                    'list_count': len(memberships),
                    'word_counts': word_counts,
                    'memberships': memberships
                })
            except Exception as e:
                self.send_json({'success': False, 'error': str(e)})
            return
            
        length = int(self.headers.get('Content-Length', 0))
        data = {}
        if length > 0:
            data = json.loads(self.rfile.read(length).decode())
        
        if parsed.path == '/api/save-config':
            self.save_config(data)
            if hasattr(DashHandler, '_ai_cache_time'): DashHandler._ai_cache_time = 0
            self.send_json({'success': True})
        elif parsed.path == '/api/save-cookies':
            COOKIES_PATH.parent.mkdir(exist_ok=True)
            with open(COOKIES_PATH, 'w') as f: json.dump(data, f)
            if hasattr(DashHandler, '_x_cache_time'): DashHandler._x_cache_time = 0
            self.send_json({'success': True})
        elif parsed.path == '/api/run':
            if not self.app_state.get('running'):
                self.app_state.update({'running': True, 'progress': 0, 'status_msg': 'Starting...', 'error': None, 'last_report': None})
                threading.Thread(target=self.run_task).start()
                self.send_json({'success': True})
            else:
                self.send_json({'success': False, 'error': 'Already running'})
        else:
            self.send_error(404)

    def load_config(self):
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r') as f: return json.load(f)
        return {
            "summarization": {"provider": "groq", "options": {
                "ollama": {"model": "qwen2.5:7b", "endpoint": "http://localhost:11434"},
                "lmstudio": {"model": "local-model", "endpoint": "http://localhost:1234/v1"},
                "groq": {"model": "llama-3.3-70b-versatile", "endpoint": "https://api.groq.com/openai/v1", "api_key": ""},
                "claude": {"model": "claude-3-5-sonnet-20240620", "api_key": ""},
                "openai": {"model": "gpt-4o", "api_key": ""}
            }},
            "twitter": {"list_urls": [], "max_tweets": 100, "list_owner": None}
        }

    def save_config(self, config):
        with open(CONFIG_PATH, 'w') as f: json.dump(config, f, indent=2)

    def save_history_metadata(self, filename, meta):
        meta_path = OUTPUT_DIR / 'history.json'
        data = {}
        if meta_path.exists():
            try:
                with open(meta_path, 'r') as f: data = json.load(f)
            except: pass
        
        data[filename] = meta
        
        # Clean up stale entries (if file doesn't exist)
        cleaned = {}
        for fname, info in data.items():
            if (OUTPUT_DIR / fname).exists():
                cleaned[fname] = info
        
        with open(meta_path, 'w') as f: json.dump(cleaned, f, indent=2)

    async def _run_async_task(self):
        start_time = time.time()
        try:
            config = self.load_config()
            print(f"🚀 [Performance] starting task at {datetime.now().strftime('%H:%M:%S')}")
            
            self.app_state.update({'status_msg': 'Initializing...', 'progress': 5, 'error': None})
            list_owner = config['twitter'].get('list_owner')
            fetcher = XListFetcher(list_owner=list_owner)
            
            t0 = time.time()
            success, msg = await fetcher.login()
            print(f"🔑 [Performance] login took {time.time()-t0:.2f}s: {msg}")
            if not success:
                if 'not found' in msg.lower() or 'no cookies' in msg.lower():
                    raise Exception("No X session found. Please go to Settings → X Account and import your cookies first.")
                elif '401' in msg or 'unauthorized' in msg.lower() or 'expired' in msg.lower():
                    raise Exception("X session expired or unauthorized. Please go to Settings → X Account and re-import your cookies.")
                elif '429' in msg or 'rate limit' in msg.lower():
                    raise Exception("X Rate Limit reached while verifying session. Please wait 15 minutes and try again.")
                else:
                    raise Exception(f"X login failed: {msg}. Please go to Settings → X Account and re-import your cookies.")

            urls = config['twitter'].get('list_urls', [])
            if not urls:
                raise Exception("No X List URLs configured. Please go to Settings and add at least one X List URL.")
            max_t = config['twitter'].get('max_tweets', 100)
            
            all_tweets = []
            async def fetch_and_update(url, i):
                return await fetcher.fetch_list_tweets(url, max_t)

            self.app_state['status_msg'] = f"Fetching {len(urls)} lists (staggered)..."
            print(f"📥 [Performance] fetching {len(urls)} lists with randomization...")
            t1 = time.time()
            
            import random
            tasks = []
            for i, url in enumerate(urls):
                # Stagger the start of each fetch by 0.3s to 1.2s
                delay = i * (0.3 + random.random() * 0.5)
                tasks.append(fetcher.fetch_list_tweets(url, max_t, delay=delay))
                
            results = await asyncio.gather(*tasks)
            for r in results: all_tweets.extend(r)
            print(f"📥 [Performance] fetching took {time.time()-t1:.2f}s ({len(all_tweets)} tweets total)")

            if not all_tweets:
                raise Exception("No tweets were fetched from any of your lists. Your X session may have expired — please go to Settings → X Account and re-import your cookies.")
            
            self.app_state['progress'] = 60
            self.app_state['status_msg'] = "Analyzing links..."
            t2 = time.time()
            agg = fetcher.aggregate_by_links(all_tweets)
            print(f"📊 [Performance] aggregation took {time.time()-t2:.2f}s")
            
            self.app_state['progress'] = 80
            self.app_state['status_msg'] = "Generating AI insights..."
            print(f"🤖 [Performance] calling {config['summarization']['provider']}...")
            t3 = time.time()
            provider = LLMProvider(config)
            summary = provider.summarize(agg)
            
            if summary.startswith("Error"):
                raise Exception(f"AI Synthesis failed: {summary}")
                
            print(f"🤖 [Performance] AI summary took {time.time()-t3:.2f}s")
            
            fname = f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            OUTPUT_DIR.mkdir(exist_ok=True)
            _prov = config['summarization']['provider']
            _model = config['summarization']['options'].get(_prov, {}).get('model', '')
            _ai_label = f"{_prov.capitalize()} \u00b7 {_model}" if _model else _prov.capitalize()
            fetcher.generate_html_report(agg, summary, OUTPUT_DIR / fname, tweet_count=len(all_tweets), ai_model=_ai_label)
            
            # Save Metadata for History
            meta = {
                'name': fetcher.list_info.get('name', 'Unknown List'),
                'username': fetcher.list_info.get('owner', 'Unknown'),
                'tweets': len(all_tweets),
                'links': len(agg['by_link']),
                'profile_img': fetcher.list_info.get('profile_image_url') or 'https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png',
                'members': fetcher.list_info.get('member_count', 0)
            }
            self.save_history_metadata(fname, meta)

            self.app_state.update({
                'progress': 100,
                'status_msg': 'Complete!',
                'last_report': fname
            })
            print(f"✅ [Performance] total run time: {time.time()-start_time:.2f}s")
        except Exception as e:
            err_msg = str(e)
            # Only rewrite generic low-level rate limit errors that weren't already formatted above
            if ('429' in err_msg or 'rate limit' in err_msg.lower()) and 'Please' not in err_msg:
                err_msg = "X Rate Limit Reached. Please wait 15 minutes before trying again."

            print(f"❌ [Critical Error] {err_msg}")
            self.app_state.update({
                'status_msg': 'Error',
                'progress': 0,
                'error': err_msg
            })
            print(f"❌ [Error] Task failed: {err_msg}")
            self.app_state.update({'error': err_msg, 'status_msg': 'Error', 'running': False})
        finally:
            self.app_state['running'] = False

    def run_task(self):
        asyncio.run(self._run_async_task())

    def get_reconstructed_html(self):
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>X List Summarizer</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0e14;
            --card: #151921;
            --header: #0f1219;
            --border: #232a35;
            --text: #eff3f4;
            --text-dim: #949ba4;
            --accent: #1d9bf0;
            --accent-hover: #1a8cd8;
            --green: #00ba7c;
            --red: #f4212e;
            --blue-tip: #1d9bf01a;
        }
        * { box-sizing: border-box; }
        body { 
            font-family: 'Inter', sans-serif; 
            background-color: var(--bg); color: var(--text); 
            margin: 0; min-height: 100vh;
        }

        /* Header Precision Alignment */
        header {
            display: flex; align-items: center; justify-content: center;
            padding: 0 40px; height: 90px;
            background: var(--header); border-bottom: 1px solid var(--border);
            position: sticky; top: 0; z-index: 100;
        }
        .main-nav {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            max-width: 1400px;
            gap: 20px;
            white-space: nowrap;
        }
        .logo-area { display: flex; align-items: center; gap: 14px; cursor: pointer; flex-shrink: 0; }
        .logo-box { 
            width: 64px; height: 64px; border-radius: 16px; 
            background: url("icon.png") center/cover;
            box-shadow: 0 0 20px rgba(29, 155, 240, 0.2);
        }
        .version { font-size: 11px; font-weight: 800; color: var(--text-dim); }

        .middle-section {
            display: flex; align-items: center; gap: 15px;
        }
        .status-container {
            background: #000;
            border: 1px solid var(--border);
            border-radius: 50px;
            padding: 5px 5px 5px 24px;
            display: flex; align-items: center; min-width: 320px;
        }
        .status-label { font-size: 13px; font-weight: 700; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 150px; }
        .inline-p-con { width: 100px; height: 4px; background: #1a1a1a; border-radius: 10px; margin: 0 20px; display: none; overflow: hidden; }
        .inline-p-bar { height: 100%; width: 0%; background: var(--accent); border-radius: 10px; transition: 0.4s; }

        .run-btn {
            background: linear-gradient(135deg, #1d9bf0 0%, #1a8cd8 100%);
            color: white; border: none; padding: 12px 28px; border-radius: 40px;
            font-weight: 800; cursor: pointer; display: flex; align-items: center; gap: 10px;
            transition: 0.2s; box-shadow: 0 5px 15px rgba(29, 155, 240, 0.35);
            font-size: 14px; margin-left: auto;
        }
        .run-btn:hover { transform: translateY(-1px); filter: brightness(1.1); }
        
        .status-pill { 
            display: flex; align-items: center; gap: 10px; font-size: 12px; font-weight: 700; 
            background: rgba(255, 255, 255, 0.05); padding: 11px 20px; border-radius: 40px;
            border: 1px solid var(--border); color: var(--text-dim);
            height: 48px; white-space: nowrap; flex-shrink: 0;
        }
        .dot { width: 8px; height: 8px; border-radius: 50%; }
        .dot.active { background: var(--green); box-shadow: 0 0 10px var(--green); }
        .dot.error { background: var(--red); box-shadow: 0 0 10px var(--red); }

        .nav-links { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
        .nav-link { 
            color: var(--text-dim); text-decoration: none; font-weight: 700; font-size: 14px; 
            cursor: pointer; transition: 0.2s;
            display: flex; align-items: center;
            padding: 10px 18px; border-radius: 12px;
            white-space: nowrap;
        }
        .nav-link:hover { color: var(--text); background: rgba(255,255,255,0.03); }
        .guide-list { margin-top: 15px; padding-left: 20px; }
        .guide-list li { margin-bottom: 10px; color: var(--text-dim); line-height: 1.6; }
        .nav-link.active { 
            color: #fff; 
            background: #1d9bf025;
            border: 1px solid #1d9bf040;
        }

        /* Settings Grid Layout */
        .container { max-width: 1200px; margin: 40px auto; padding: 0 40px; }
        .settings-grid { display: grid; grid-template-columns: 1fr 360px; gap: 40px; align-items: start; }
        
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 24px; padding: 32px; margin-bottom: 32px; }
        .sec-title { display: flex; align-items: center; gap: 12px; font-size: 20px; font-weight: 700; margin-bottom: 25px; }
        
        label { display: block; font-size: 13px; font-weight: 600; color: var(--text-dim); margin-bottom: 12px; }
        input, select, textarea { 
            width: 100%; background: #080a0f; border: 1px solid var(--border); color: var(--text); 
            padding: 15px 18px; border-radius: 12px; margin-bottom: 20px; font-family: inherit; font-size: 14px;
        }
        input:focus, textarea:focus { border-color: var(--accent); outline: none; }
        .hint { font-size: 12px; color: var(--text-dim); margin-top: -15px; margin-bottom: 20px; display: block; }

        /* Sync Tip Box */
        .tip-box { 
            background: var(--blue-tip); border: 1px solid #1d9bf030; border-radius: 12px; 
            padding: 20px; margin-bottom: 25px; 
        }
        .tip-title { color: var(--accent); font-weight: 700; font-size: 13px; margin-bottom: 12px; }
        .tip-list { margin: 0; padding-left: 18px; font-size: 12px; color: var(--text-dim); line-height: 1.8; }

        .btn-full { width: 100%; justify-content: center; }
        .btn-save { background: #ffffff08; border: 1px solid var(--border); color: #fff; }
        .btn-save:hover { background: #ffffff12; }

        /* ProgressOverlay */
        #progress-overlay {
            position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: none; align-items: center; justify-content: center; z-index: 1000;
        }
        .p-box { width: 440px; background: var(--card); border: 1px solid var(--border); padding: 48px; border-radius: 32px; text-align: center; }
        .p-bar-con { height: 8px; background: #000; border-radius: 10px; margin: 30px 0; overflow: hidden; }
        .p-bar { height: 100%; background: var(--accent); width: 0%; transition: 0.4s; }

        /* Storage & History Styling */
        .storage-card { 
            border: 1px dashed #1d9bf080; background: rgba(29, 155, 240, 0.04); 
            border-radius: 12px; padding: 40px; margin-bottom: 50px;
        }
        .path-display { 
            background: #000; border: 1px solid var(--border); border-radius: 8px; 
            padding: 18px 25px; font-family: 'Consolas', monospace; font-size: 13px; color: var(--text-dim);
            margin: 25px 0; width: 100%;
        }
        .history-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
        .history-title { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 14px; }
        .report-count { font-size: 13px; color: var(--text-dim); }

        .report-card { 
            background: #151921; border: 1px solid var(--border); border-radius: 16px; 
            padding: 30px 40px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center;
        }
        .report-info .r-title { font-weight: 800; font-size: 19px; color: var(--accent); margin-bottom: 10px; display: block; }
        .report-info .r-date { font-size: 14px; color: var(--text-dim); font-weight: 500; }
        
        .report-actions { display: flex; gap: 15px; }
        .btn-action { 
            background: #1e232b; border: 1px solid #2d343f; color: var(--text);
            padding: 10px 22px; border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer;
            transition: 0.2s; display: flex; align-items: center; gap: 10px;
        }
        .btn-action:hover { background: #252b36; border-color: #3d4654; }
        .icon-small { font-size: 14px; opacity: 0.8; }
        .h-img { width: 44px; height: 44px; border-radius: 50%; border: 1px solid var(--border); margin-right: 15px; flex-shrink: 0; }
        .report-info-con { display: flex; align-items: center; flex: 1; }

        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeIn 0.3s ease-out; }
        
        /* Profiler Word Cloud Styles */
        .cloud-word {
            transition: 0.3s;
            cursor: pointer;
            padding: 8px 15px;
            border-radius: 12px;
            display: inline-block;
            font-weight: 700;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.05);
            user-select: none;
        }
        .cloud-word:hover {
            transform: scale(1.15) rotate(2deg);
            background: rgba(29, 155, 240, 0.15);
            border-color: var(--accent);
            color: #fff !important;
            z-index: 10;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }
        .cloud-word.active {
            background: var(--accent);
            color: #fff !important;
            border-color: var(--accent);
            box-shadow: 0 0 20px rgba(29, 155, 240, 0.4);
        }

        .prof-detail-card {
            background: rgba(0,0,0,0.3);
            border: 1px solid var(--border);
            border-radius: 16px;
            overflow: hidden;
            margin-top: 25px;
            animation: fadeIn 0.4s ease-out;
        }
        .prof-table { width: 100%; border-collapse: collapse; }
        .prof-table th { background: rgba(255,255,255,0.03); padding: 15px; text-align: left; font-size: 11px; text-transform: uppercase; color: var(--text-dim); }
        .prof-table td { padding: 15px; border-top: 1px solid var(--border); font-size: 14px; }
        .prof-table tr:hover { background: rgba(255,255,255,0.02); }
        .word-tag { 
            background: var(--accent); color: #fff; padding: 4px 12px; border-radius: 20px; 
            font-size: 13px; font-weight: 800; display: inline-block; margin-bottom: 20px;
        }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes floatIn { from { opacity: 0; transform: scale(0.5) translateZ(-100px); } to { opacity: 1; transform: scale(1) translateZ(0); } }

        /* Modal Styles */
        .modal {
            display: none;
            position: fixed;
            z-index: 2000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.9);
            backdrop-filter: blur(10px);
            cursor: zoom-out;
            align-items: center; justify-content: center;
        }
        .modal-content {
            margin: auto;
            display: block;
            max-width: 90%;
            max-height: 90%;
            border-radius: 12px;
            box-shadow: 0 0 50px rgba(0,0,0,0.5);
            cursor: default;
        }
        .close-modal {
            position: absolute;
            top: 30px;
            right: 50px;
            color: #fff;
            font-size: 40px;
            font-weight: bold;
            cursor: pointer;
        }
        .enlarge-hint {
            font-size: 11px;
            color: var(--accent);
            text-align: center;
            margin-top: 8px;
            font-weight: 700;
            cursor: pointer;
        }

        /* Ranking Modal Specifics */
        .rank-table { width: 100%; border-collapse: collapse; margin-top: 20px; color: var(--text); }
        .rank-table th { text-align: left; padding: 12px; border-bottom: 2px solid var(--border); color: var(--accent); font-size: 13px; text-transform: uppercase; }
        .rank-table td { padding: 15px 12px; border-bottom: 1px solid var(--border); font-size: 14px; line-height: 1.4; }
        .rank-num { font-weight: 800; color: var(--accent); font-size: 18px; }
        .info-trigger { 
            cursor: pointer; width: 22px; height: 22px; border-radius: 50%; 
            background: var(--accent-dim); color: var(--accent); 
            display: inline-flex; align-items: center; justify-content: center; 
            font-size: 14px; font-weight: 800; border: 1px solid #1d9bf030;
            transition: 0.2s;
        }
        .info-trigger:hover { background: var(--accent); color: #fff; transform: scale(1.1); }
        
    </style>
</head>
<body>
    <header>
        <div class="main-nav">
            <div class="logo-area" onclick="resetApp()">
                <div class="logo-box"></div>
            </div>

            <div class="middle-section">
                <div class="status-container">
                    <span class="status-label" id="run-status">Preparing...</span>
                    <div class="inline-p-con" id="inline-progress">
                        <div class="inline-p-bar" id="inline-p-bar"></div>
                    </div>
                    <button class="run-btn" id="run-btn" onclick="startAnalysis()">
                        <span style="font-size:11px;">▶</span> Run Analysis
                    </button>
                </div>
                
                <div class="status-pill">
                    <div id="ai-dot" class="dot active"></div>
                    AI: <span id="ai-txt">Ready</span>
                </div>
                <div class="status-pill">
                    <div id="x-dot" class="dot active"></div>
                    X Auth: <span id="x-txt">OK</span>
                </div>
            </div>

            <div class="nav-links">
                <a class="nav-link active" id="nav-home" href="javascript:void(0)" onclick="showTab('home')">Dashboard</a>
                <a class="nav-link" id="nav-report" href="javascript:void(0)" onclick="viewLatest()">Report</a>
                <a class="nav-link" id="nav-history" href="javascript:void(0)" onclick="showTab('history')">History</a>
                <a class="nav-link" id="nav-profiler" href="javascript:void(0)" onclick="showTab('profiler')">Profiler</a>
                <a class="nav-link" id="nav-settings" href="javascript:void(0)" onclick="showTab('settings')">Settings</a>
            </div>
        </div>
    </header>

    <div id="home" class="container tab-content active">
        <div style="text-align:center; margin: 60px 0 80px;">
            <h1 style="font-size: 52px; font-weight: 800; margin-bottom: 25px;">X List Summarizer <span style="font-size: 18px; opacity: 0.6; font-weight: 600; margin-left: 10px;">v1.6.0</span></h1>
            <p style="font-size: 18px; color: var(--text-dim); line-height: 1.6; max-width: 650px; margin: 0 auto;">Turn the noise of X into actionable intelligence. This premium tool analyzes curated lists to extract high-signal trends and media.</p>
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px;">
            <div class="card" style="padding: 35px; border-radius: 28px;">
                <span style="font-size: 36px; display: block; margin-bottom: 20px;">🔍</span>
                <div style="font-weight: 800; font-size: 19px; margin-bottom: 12px;">Deep Extraction</div>
                <div style="font-size: 14px; color: var(--text-dim); line-height: 1.6;">Recursively scans Retweets and Quote Tweets to capture shared links and deduplicated media, ensuring no high-signal content is missed.</div>
            </div>
            <div class="card" style="padding: 35px; border-radius: 28px;">
                <span style="font-size: 36px; display: block; margin-bottom: 20px;">📈</span>
                <div style="font-weight: 800; font-size: 19px; margin-bottom: 12px;">Power Scoring</div>
                <div style="font-size: 14px; color: var(--text-dim); line-height: 1.6;">Identifies trending topics via a weighted algorithm (Likes + RTs + Replies + Quotes + Bookmarks) to filter out low-value noise.</div>
            </div>
            <div class="card" style="padding: 35px; border-radius: 28px;">
                <span style="font-size: 36px; display: block; margin-bottom: 20px;">🤖</span>
                <div style="font-weight: 800; font-size: 19px; margin-bottom: 12px;">AI Intelligence</div>
                <div style="font-size: 14px; color: var(--text-dim); line-height: 1.6;">Harnesses xAI Grok, Claude, and Llama 3 to synthesize hundreds of posts into structured reports with explicit model labeling.</div>
            </div>
        </div>
        <div class="card" style="text-align: center; background: linear-gradient(135deg, rgba(29,155,240,0.06), rgba(29,155,240,0.02)); border: 1px solid rgba(29,155,240,0.15); margin-top: 40px; padding: 45px;">
            <div style="font-weight: 800; font-size: 20px; margin-bottom: 15px;">Ready to begin?</div>
            <div style="font-size: 15px; color: var(--text-dim);">Ensure your <strong>X Authentication</strong> and <strong>AI Model</strong> are configured in Settings, then click <strong>Run Analysis</strong> in the header to start.</div>
        </div>
    </div>

    <div id="profiler" class="container tab-content">
        <div style="text-align:center; margin-bottom: 40px;">
            <h1 style="font-size: 42px; font-weight: 800; margin-bottom: 15px;">Account Profiler</h1>
            <p style="color: var(--text-dim); font-size: 16px;">See how any account is categorized by the X community via list membership analysis.</p>
        </div>

        <div class="card" style="padding: 40px; text-align: center; background: linear-gradient(135deg, #151921 0%, #0b0e14 100%);">
            <div style="max-width: 500px; margin: 0 auto;">
                <div style="font-weight: 800; font-size: 18px; margin-bottom: 20px;">Search X Username</div>
                <div style="position: relative; display: flex; gap: 10px;">
                    <span style="position: absolute; left: 20px; top: 50%; transform: translateY(-50%); color: var(--accent); font-weight: 800; font-size: 18px;">@</span>
                    <input type="text" id="prof_user" placeholder="username" style="width: 100%; background: #000; border: 1px solid var(--border); padding: 16px 16px 16px 45px; border-radius: 12px; color: #fff; font-size: 16px; font-weight: 600; margin-bottom: 0;">
                    <button onclick="generateProfile()" id="prof_btn" class="run-btn" style="margin: 0; padding: 0 30px;">Analyze</button>
                </div>
            </div>
        </div>

        <div id="prof_results" style="display: none; margin-top: 30px;">
            <div class="card" style="padding: 30px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; border-bottom: 1px solid var(--border); padding-bottom: 20px;">
                    <div>
                        <div style="font-size: 13px; color: var(--text-dim); font-weight: 800; text-transform: uppercase; letter-spacing: 1px;">Analysis Results for</div>
                        <div id="prof_res_user" style="font-size: 24px; font-weight: 800; color: var(--accent);">@username</div>
                    </div>
                    <div style="text-align: right;">
                        <div id="prof_res_count" style="font-size: 24px; font-weight: 800; color: var(--text);">0</div>
                        <div style="font-size: 11px; color: var(--text-dim); font-weight: 800; text-transform: uppercase;">List Memberships</div>
                        <a id="prof_x_link" href="#" target="_blank" style="font-size: 10px; color: var(--accent); text-decoration: none; font-weight: 800; display: none; margin-top: 5px;">VIEW ON X ↗</a>
                    </div>
                </div>
                
                <div id="word_cloud" style="min-height: 440px; display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 15px; padding: 30px; background: rgba(0,0,0,0.2); border-radius: 20px; position: relative; overflow: hidden; perspective: 1000px;">
                    <!-- Words will be injected here -->
                </div>

                <div id="prof_details" style="display: none; margin-top: 30px; border-top: 1px dashed var(--border); padding-top: 30px;">
                    <!-- List details will be injected here -->
                </div>
            </div>
        </div>
    </div>

    <div id="settings" class="container tab-content">
        <div style="margin-bottom: 30px; display: flex; gap: 15px; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 25px;">
            <div style="font-weight: 800; font-size: 20px;">⚙️ App Settings</div>
            <div style="margin-left: auto; display: flex; gap: 8px;">
                <a href="javascript:void(0)" onclick="toggleMethodology()" id="meth_toggle_btn" class="btn-action" style="font-size: 11px; padding: 6px 14px; background: #1d9bf020; border-color: #1d9bf040; color: #fff;">🧠 View Methodology</a>
            </div>
        </div>

        <!-- Integrated Methodology Section (Collapsible) -->
        <div id="methodology_sec" style="margin-bottom: 40px; border-bottom: 1px solid var(--border); padding-bottom: 40px; display: none;">
            <div style="text-align: center; margin-bottom: 40px; position: relative;">
                <button onclick="toggleMethodology(false)" style="position: absolute; right: 0; top: 0; background: transparent; border: 1px solid var(--border); color: var(--text-dim); padding: 8px 15px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 700;">✖ Close</button>
                <h2 style="font-size: 28px; font-weight: 800; margin-bottom: 10px;">Methodology & Under-the-Hood</h2>
                <p style="color: var(--text-dim); font-size: 14px;">Understanding how the X List Summarizer processes your data for maximum signal.</p>
            </div>

            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 24px;">
                <div class="card" style="margin-bottom: 0; padding: 25px;" id="meth_1">
                    <div style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--accent); display: flex; align-items: center; gap: 10px;">
                        <span>📊</span> 1. Smart Fetching & Extraction
                    </div>
                    <p style="color: var(--text-dim); line-height: 1.5; font-size: 13px;">The app follows a <strong>"Latest-First"</strong> approach, fetching the newest content backward through history.</p>
                    <ul class="guide-list" style="font-size: 12px;">
                        <li><strong>Deep Link Extraction:</strong> We recursively scan <strong>Retweets and Quote Tweets</strong> to ensure shared links are tracked even when discussed indirectly.</li>
                        <li><strong>Deduplication:</strong> If the same tweet appears in multiple lists, it is only counted once for engagement math.</li>
                    </ul>
                </div>

                <div class="card" style="margin-bottom: 0; padding: 25px;" id="meth_2">
                    <div style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--accent); display: flex; align-items: center; gap: 10px;">
                        <span>🧠</span> 2. Weighted Ranking
                    </div>
                    <p style="color: var(--text-dim); line-height: 1.5; font-size: 13px;">Low-signal noise is filtered using a weighted scoring algorithm for every grouped link:</p>
                    <div style="background: #000; padding: 10px; border-radius: 6px; font-family: monospace; font-size: 11px; color: var(--accent); margin: 12px 0; text-align: center;">
                        Likes + (RTs*1.5) + (Replies*2.0) + Quotes + Bookmarks
                    </div>
                    <ul class="guide-list" style="font-size: 12px;">
                        <li><strong>Report Visibility:</strong> The top 30 filtered link-groups are displayed in your report.</li>
                        <li><strong>AI Focus:</strong> We feed the top 20 groups to the AI for synthesis to ensure razor-sharp accuracy.</li>
                    </ul>
                </div>

                <div class="card" style="margin-bottom: 0; padding: 25px;" id="meth_3">
                    <div style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--accent); display: flex; align-items: center; gap: 10px;">
                        <span>🎞️</span> 3. Media Deduplication
                    </div>
                    <p style="color: var(--text-dim); line-height: 1.5; font-size: 13px;">Reports are kept lightweight and professional through advanced media handling:</p>
                    <ul class="guide-list" style="font-size: 12px;">
                        <li><strong>Group Deduplication:</strong> Identical images or videos shared multiple times in a retweet chain are rendered only once per cluster.</li>
                        <li><strong>Click-to-Play:</strong> To bypass X's session-based video authentication, we render videos as clickable thumbnails that open the native tweet on X.</li>
                    </ul>
                </div>

                <div class="card" style="margin-bottom: 0; padding: 25px;" id="meth_4">
                    <div style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--accent); display: flex; align-items: center; gap: 10px;">
                        <span>🤖</span> 4. Transparent AI Synthesis
                    </div>
                    <p style="color: var(--text-dim); line-height: 1.5; font-size: 13px;">The AI synthesizes the messy stream of raw tweets into cohesive narrative themes:</p>
                    <ul class="guide-list" style="font-size: 12px;">
                        <li><strong>Model Labeling:</strong> Reports now explicitly state the exact provider and model (e.g. Grok-3, Llama-3.3) used for the analysis.</li>
                        <li><strong>Domain Insight:</strong> Section C calculates the mention count and sentiment for trending domains.</li>
                    </ul>
                </div>
            </div>
            <div style="text-align: center; margin-top: 30px;">
                <button class="run-btn" style="background: rgba(255,255,255,0.05); border: 1px solid var(--border); font-size: 12px; padding: 10px 25px;" onclick="toggleMethodology(false)">✖ Close Methodology</button>
            </div>
        </div>

        <div class="settings-grid">
            <div class="left-col">
                <div class="card">
                    <div class="sec-title">📝 Lists & Sources</div>
                    <label>X List URLs (One per line)</label>
                    <textarea id="s_urls" rows="6" style="resize: none;"></textarea>
                    
                    <label>Max Tweets per List</label>
                    <input type="number" id="s_max" value="100">

                    <label>List Owner Username (Optional)</label>
                    <input type="text" id="s_owner" placeholder="Scobleizer">
                    <span class="hint">Use this if the owner is shown as "Unknown" in reports.</span>
                </div>

                <div class="card">
                    <div class="sec-title" style="justify-content: space-between;">
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <span>🤖</span> AI Intelligence
                        </div>
                        <div class="info-trigger" onclick="openRankingModal()">?</div>
                    </div>
                    <label>Provider</label>
                    <select id="s_prov" onchange="renderProviderOptions()">
                        <option value="groq">Groq (Free Cloud)</option>
                        <option value="ollama">Ollama (Local)</option>
                        <option value="lmstudio">LM Studio (Local)</option>
                        <option value="claude">Anthropic Claude</option>
                        <option value="openai">OpenAI GPT-4o</option>
                        <option value="gemini">Google Gemini</option>
                        <option value="deepseek">DeepSeek (V3)</option>
                        <option value="grok">xAI Grok</option>
                        <option value="openrouter">OpenRouter (All Models)</option>
                    </select>

                    <div id="ai_help" class="tip-box" style="margin-top: -10px; margin-bottom: 25px; display: none;"></div>

                    <label>Model Name</label>
                    <select id="p_mod_select" onchange="toggleCustomModel()"></select>
                    <input type="text" id="p_mod_custom" placeholder="Enter custom model name..." style="display:none; margin-top: -10px;">

                    <div id="p_key_con">
                        <label>API Key</label>
                        <input type="password" id="p_key" placeholder="••••••••••••••••••••••••••••••••••••••••••••••••">
                    </div>

                    <button class="run-btn btn-full btn-save" onclick="saveConfig()">
                        <span>💾</span> Save App Configuration
                    </button>
                </div>
            </div>

            <div class="right-col" id="auth_sec">
                <div class="card">
                    <div class="sec-title">🔑 X Authentication</div>
                    <p style="font-size: 13px; color: var(--text-dim); line-height: 1.6; margin-bottom: 25px;">
                        To fetch tweets from private lists or avoid rate limits, please provide your session cookies from a logged-in X.com session.
                    </p>

                    <label>auth_token</label>
                    <input type="password" id="s_token" placeholder="Paste auth_token">

                    <label>ct0</label>
                    <input type="text" id="s_ct0" placeholder="Paste ct0">

                    <div class="tip-box">
                        <div class="tip-title">How to find these:</div>
                        <ul class="tip-list">
                            <li>Log in to <strong>x.com</strong> in Chrome/Edge</li>
                            <li>Press <strong>F12</strong> > <strong>Application</strong> tab</li>
                            <li>Under <strong>Cookies</strong>, select <strong>https://x.com</strong></li>
                            <li>Copy values for <strong>auth_token</strong> and <strong>ct0</strong></li>
                        </ul>
                        <img src="screenshots/auth_guide.png" onclick="openModal(this.src)" style="width: 100%; border-radius: 8px; margin-top: 15px; border: 1px solid var(--border); cursor: zoom-in;">
                        <div class="enlarge-hint" onclick="openModal('screenshots/auth_guide.png')">🔍 Click to enlarge image</div>
                    </div>

                </div>
            </div>
        </div>


    </div>

    <div id="history" class="container tab-content">
        <div class="storage-card">
            <div style="font-weight: 800; font-size: 16px; display: flex; align-items: center; gap: 12px;">
                <span style="color:#ffcc00; font-size: 18px;">📁</span> Storage Location
            </div>
            <p style="font-size: 14px; color: var(--text-dim); margin-top: 15px;">All your generated reports are stored on your local drive at:</p>
            <div id="storage-path" class="path-display">C:\...</div>
            <button class="run-btn" style="padding: 12px 24px; font-size: 13px;" onclick="openFolder()">
                🚀 Open Output Folder
            </button>
        </div>

        <div class="history-row">
            <div class="history-title"><span style="font-size: 20px;">📄</span> Report History</div>
            <div id="report-stats" class="report-count">Showing reports 0 - 0 of 0</div>
        </div>
        
        <div id="history-grid"></div>
    </div>



    <div id="report" class="container tab-content" style="max-width: 100%; padding: 0;">
        <iframe id="report-frame" style="width: 100%; height: calc(100vh - 90px); border: none;"></iframe>
    </div>

    <script>
        let cfg = { summarization: { options: {} }, twitter: { list_urls: [] } };
        
        // Critical: Ensure functions are available globally before everything else
        window.showTab = function(t) {
            console.log("Switching to tab:", t);
            const tabs = document.querySelectorAll('.tab-content');
            const navs = document.querySelectorAll('.nav-link');
            
            tabs.forEach(x => x.classList.remove('active'));
            navs.forEach(x => x.classList.remove('active'));
            
            const targetTab = document.getElementById(t);
            const targetNav = document.getElementById('nav-' + t);
            
            if (targetTab) targetTab.classList.add('active');
            if (targetNav) targetNav.classList.add('active');
            
            if (t === 'history') loadHistory().catch(e => console.error("History error:", e));
        };

        function resetApp() {
            const frame = document.getElementById('report-frame');
            if (frame) frame.src = 'about:blank';
            window.showTab('home');
        }

        let reportOpened = false;
        let lastKnownReport = null;
        
        async function poll() {
            try {
                const r = await fetch('/api/status');
                const s = await r.json();
                
                // Update status indicators
                document.getElementById('ai-dot').className = 'dot ' + (s.ai_status.active ? 'active' : 'error');
                document.getElementById('ai-txt').innerText = s.ai_status.message;
                document.getElementById('x-dot').className = 'dot ' + (s.x_auth.active ? 'active' : 'error');
                document.getElementById('x-txt').innerText = s.x_auth.message;
                
                const statusEl = document.getElementById('run-status');
                const progressCon = document.getElementById('inline-progress');
                const progressBar = document.getElementById('inline-p-bar');
                const runBtn = document.getElementById('run-btn');

                // CRITICAL: Capture and auto-open report as soon as we see it
                if (s.last_report && s.last_report !== lastKnownReport) {
                    lastKnownReport = s.last_report;
                    if (!reportOpened) {
                        console.log("Report detected! Auto-opening:", s.last_report);
                        reportOpened = true;
                        loadInAppReport(s.last_report);
                    }
                }

                // Handle UI states
                if (s.error) {
                    statusEl.innerText = 'Error: ' + s.error;
                    statusEl.style.color = 'var(--red)';
                    statusEl.style.maxWidth = '400px';
                    progressCon.style.display = 'none';
                    runBtn.innerText = '✖ Clear';
                    runBtn.onclick = () => { fetch('/api/reset-progress'); location.reload(); };
                    runBtn.style.filter = 'none';
                    runBtn.disabled = false;
                } else if (s.running) {
                    statusEl.innerText = s.status_msg;
                    statusEl.style.color = 'var(--text-dim)';
                    progressCon.style.display = 'block';
                    progressBar.style.width = s.progress + '%';
                    runBtn.style.filter = 'grayscale(1) opacity(0.5)';
                    runBtn.disabled = true;
                    runBtn.innerHTML = '<span style="font-size:11px;">▶</span> Run Analysis';
                    runBtn.onclick = null;
                } else if (s.progress === 100) {
                    statusEl.innerText = 'Complete!';
                    statusEl.style.color = 'var(--green)';
                    progressCon.style.display = 'none';
                    runBtn.style.filter = 'none';
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<span style="font-size:11px;">▶</span> Run Analysis';
                    runBtn.onclick = startAnalysis;
                    
                    // Reset progress after delay
                    setTimeout(() => { fetch('/api/reset-progress'); }, 3000);
                } else {
                    statusEl.innerText = 'Ready';
                    statusEl.style.color = 'var(--text-dim)';
                    progressCon.style.display = 'none';
                    runBtn.style.filter = 'none';
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<span style="font-size:11px;">▶</span> Run Analysis';
                    runBtn.onclick = startAnalysis;
                }
            } catch(e) { console.error('Poll error:', e); }
        }
 
        function openModal(src) {
            const modal = document.getElementById('imageModal');
            const modalImg = document.getElementById('modalImg');
            modal.style.display = "flex";
            modalImg.src = src;
        }

        function closeModal() {
            document.getElementById('imageModal').style.display = "none";
        }

        async function viewLatest() {
            try {
                const r = await fetch('/api/status');
                const s = await r.json();
                const reportName = s.last_report || 'latest';
                loadInAppReport(reportName);
                const overlay = document.getElementById('progress-overlay');
                if (overlay) overlay.style.display = 'none';
            } catch(e) { console.error('viewLatest error:', e); }
        }

        function loadInAppReport(name) {
            const frame = document.getElementById('report-frame');
            frame.src = '/output/' + name;
            showTab('report');
        }

        async function loadConfig() {
            try {
                const r = await fetch('/api/config');
                cfg = await r.json();
                document.getElementById('s_urls').value = (cfg.twitter.list_urls || []).join('\\n');
                document.getElementById('s_max').value = cfg.twitter.max_tweets;
                document.getElementById('s_prov').value = cfg.summarization.provider;
                document.getElementById('s_owner').value = cfg.twitter.list_owner || '';
                renderProviderOptions();
            } catch(e) { console.error('loadConfig error:', e); }
        }

        function toggleCustomModel() {
            const sel = document.getElementById('p_mod_select');
            const custom = document.getElementById('p_mod_custom');
            if (sel.value === 'custom') {
                custom.style.display = 'block';
            } else {
                custom.style.display = 'none';
            }
        }

        function renderProviderOptions() {
            const p = document.getElementById('s_prov').value;
            const data = cfg.summarization.options[p] || {};
            const sel = document.getElementById('p_mod_select');
            const custom = document.getElementById('p_mod_custom');
            
            const presets = {
                'groq': ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'openai/gpt-oss-120b'],
                'claude': ['claude-sonnet-4-6', 'claude-opus-4-6', 'claude-haiku-4-5'],
                'openai': ['gpt-4o', 'gpt-4.1', 'gpt-4o-mini', 'gpt-5'],
                'gemini': ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro', 'gemini-3-flash-preview'],
                'deepseek': ['deepseek-chat', 'deepseek-reasoner'],
                'grok': ['grok-3-latest', 'grok-2-latest', 'grok-beta'],
                'openrouter': ['google/gemini-2.5-flash', 'anthropic/claude-sonnet-4-6', 'deepseek/deepseek-chat', 'meta-llama/llama-3.3-70b-instruct'],
                'ollama': ['qwen2.5:7b', 'llama3.1', 'mistral', 'phi3'],
                'lmstudio': ['local-model']
            };

            const models = presets[p] || [];
            sel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('') + '<option value="custom">Custom...</option>';
            
            if (models.includes(data.model)) {
                sel.value = data.model;
                custom.style.display = 'none';
            } else if (data.model) {
                sel.value = 'custom';
                custom.value = data.model;
                custom.style.display = 'block';
            } else {
                sel.value = models[0] || 'custom';
                toggleCustomModel();
            }

            if(document.getElementById('p_key')) document.getElementById('p_key').value = data.api_key || '';
            const keyCon = document.getElementById('p_key_con');
            if (p === 'ollama' || p === 'lmstudio') {
                keyCon.style.display = 'none';
            } else {
                keyCon.style.display = 'block';
            }

            const helpEl = document.getElementById('ai_help');
            const helpTexts = {
                'groq': '<strong>Setup Groq (Free Cloud):</strong><br>1. Get an API key from the <a href="https://console.groq.com/keys" target="_blank" style="color:var(--accent);">Groq Console</a>.<br>2. Recommended: <code>llama-3.3-70b-versatile</code> (fast, 128K context) or <code>openai/gpt-oss-120b</code> (highest capability)',
                'ollama': '<strong>Setup Ollama (Local):</strong><br>1. Ensure <a href="https://ollama.com" target="_blank" style="color:var(--accent);">Ollama</a> is running.<br>2. Run <code>ollama pull qwen2.5:7b</code> in your terminal.',
                'lmstudio': '<strong>Setup LM Studio (Local):</strong><br>1. Download <a href="https://lmstudio.ai/" target="_blank" style="color:var(--accent);">LM Studio</a>.<br>2. Load a model (e.g., <code>Qwen 2.5 7B</code>) and click <strong>Start Server</strong>.<br>3. Default endpoint: <code>http://localhost:1234/v1</code>',
                'claude': '<strong>Setup Claude:</strong><br>1. Get an API key from the <a href="https://console.anthropic.com/settings/keys" target="_blank" style="color:var(--accent);">Anthropic Console</a>.<br>2. Recommended: <code>claude-sonnet-4-6</code> (default, 1M context) or <code>claude-opus-4-6</code> (most powerful)',
                'openai': '<strong>Setup OpenAI:</strong><br>1. Get an API key from the <a href="https://platform.openai.com/api-keys" target="_blank" style="color:var(--accent);">OpenAI Platform</a>.<br>2. Recommended: <code>gpt-4.1</code> (best value) or <code>gpt-5</code> (most capable)',
                'gemini': '<strong>Setup Google Gemini:</strong><br>1. Get an API key from <a href="https://aistudio.google.com/app/apikey" target="_blank" style="color:var(--accent);">Google AI Studio</a>.<br>2. Recommended: <code>gemini-2.5-flash</code> (Fast, 1M context, reasoning)',
                'deepseek': '<strong>Setup DeepSeek:</strong><br>1. Get an API key from <a href="https://platform.deepseek.com/" target="_blank" style="color:var(--accent);">DeepSeek Platform</a>.<br>2. Recommended: <code>deepseek-chat</code> (V3, general) or <code>deepseek-reasoner</code> (chain-of-thought)',
                'grok': '<strong>Setup xAI Grok:</strong><br>1. Get an API key from the <a href="https://console.x.ai/" target="_blank" style="color:var(--accent);">xAI Console</a>.<br>2. Recommended: <code>grok-3-latest</code> (latest flagship) or <code>grok-2-latest</code> (fast, cost-effective). Earns 20% xAI credit back on X API spend.',
                'openrouter': '<strong>Setup OpenRouter:</strong><br>1. Get an API key from <a href="https://openrouter.ai/keys" target="_blank" style="color:var(--accent);">OpenRouter</a>.<br>2. Access any model through a single API key. Recommended: <code>google/gemini-2.5-flash</code>'
            };
            
            if (helpTexts[p]) {
                helpEl.innerHTML = '<div class="tip-title">Provider Guide:</div><div style="font-size:12px; line-height:1.6; color:var(--text-dim);">' + helpTexts[p] + '</div>';
                helpEl.style.display = 'block';
            } else {
                helpEl.style.display = 'none';
            }
        }

        async function saveConfig() {
            const p = document.getElementById('s_prov').value;
            const newCfg = { ...cfg };
            newCfg.summarization.provider = p;
            newCfg.twitter.list_urls = document.getElementById('s_urls').value.split('\\n').filter(x => x.trim());
            newCfg.twitter.max_tweets = parseInt(document.getElementById('s_max').value);
            newCfg.twitter.list_owner = document.getElementById('s_owner').value || null;
            
            const sel = document.getElementById('p_mod_select');
            const custom = document.getElementById('p_mod_custom');
            newCfg.summarization.options[p].model = (sel.value === 'custom') ? custom.value : sel.value;
            
            newCfg.summarization.options[p].api_key = document.getElementById('p_key').value;
            await fetch('/api/save-config', { method: 'POST', body: JSON.stringify(newCfg) });
            alert('Settings Saved');
        }

        async function saveCookies() {
            const cookies = { auth_token: document.getElementById('s_token').value, ct0: document.getElementById('s_ct0').value };
            await fetch('/api/save-cookies', { method: 'POST', body: JSON.stringify(cookies) });
            alert('Authentication Updated');
        }

        async function loadHistory() {
            const r = await fetch('/api/history');
            const data = await r.json();
            
            const sr = await fetch('/api/status');
            const s = await sr.json();
            document.getElementById('storage-path').innerText = s.output_path;
            
            const count = data.length;
            document.getElementById('report-stats').innerText = `Showing reports 1 - ${Math.min(count, 10)} of ${count}`;
            
            document.getElementById('history-grid').innerHTML = data.map(h => {
                const dateObj = new Date(h.date.replace(' ', 'T'));
                // Format: February 02, 2026 at 21:26:05
                const formattedDate = dateObj.toLocaleDateString('en-US', { 
                    month: 'long', day: '2-digit', year: 'numeric' 
                }) + ' at ' + dateObj.toLocaleTimeString('en-US', { hour12: false });

                return `
                <div class="report-card">
                    <div class="report-info-con">
                        <img src="${h.profile_img || 'https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png'}" class="h-img" onerror="this.src='https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png'">
                        <div class="report-info">
                            <span class="r-title">${h.name}</span>
                            <div style="font-size: 13px; color: var(--text-dim); margin-bottom: 8px; font-weight: 600;">
                                @${h.username} • ${h.tweets} tweets & ${h.links} links • ${h.members} members
                            </div>
                            <span class="r-date">${formattedDate}</span>
                        </div>
                    </div>
                    <div class="report-actions">
                        <button class="btn-action" onclick="loadInAppReport('${h.filename}')">
                            Preview <span class="icon-small">👁️</span>
                        </button>
                        <button class="btn-action" onclick="window.open('/output/${h.filename}', '_blank')">
                            External <span class="icon-small">↗️</span>
                        </button>
                    </div>
                </div>
            `}).join('');
        }

        let currentMemberships = [];

        async function generateProfile() {
            const user = document.getElementById('prof_user').value.trim();
            if (!user) return alert('Please enter a username');
            
            const btn = document.getElementById('prof_btn');
            const results = document.getElementById('prof_results');
            const cloud = document.getElementById('word_cloud');
            const details = document.getElementById('prof_details');
            
            btn.disabled = true;
            btn.innerText = 'Analyzing...';
            results.style.display = 'none';
            details.style.display = 'none';
            cloud.innerHTML = '';
            
            try {
                const r = await fetch('/api/profile', {
                    method: 'POST',
                    body: JSON.stringify({ username: user })
                });
                const d = await r.json();
                
                if (!d.success) throw new Error(d.error);
                
                currentMemberships = d.memberships || [];
                
                document.getElementById('prof_res_user').innerText = '@' + d.username;
                document.getElementById('prof_res_count').innerText = d.list_count || 0;
                
                const xLink = document.getElementById('prof_x_link');
                xLink.href = `https://x.com/${d.username}/lists/memberships`;
                xLink.style.display = 'block';
                
                // Render Word Cloud
                const counts = d.word_counts;
                const words = Object.keys(counts);
                
                if (words.length === 0) {
                    cloud.innerHTML = '<div style="color: var(--text-dim); font-weight: 600;">No lists found for this account.</div>';
                } else {
                    const maxCount = Math.max(...Object.values(counts));
                    const colors = ['#1d9bf0', '#00ba7c', '#ffd400', '#f91880', '#7856ff', '#ff7a00'];
                    
                    words.forEach((w, i) => {
                        const count = counts[w];
                        const size = 14 + (count / maxCount) * 36; // Scale between 14px and 50px
                        const color = colors[i % colors.length];
                        const opacity = 0.5 + (count / maxCount) * 0.5;
                        
                        const span = document.createElement('span');
                        span.className = 'cloud-word';
                        span.innerText = w;
                        span.style.fontSize = size + 'px';
                        span.style.color = color;
                        span.style.opacity = opacity;
                        span.style.animation = `floatIn 0.5s ease-out ${i * 0.02}s both`;
                        
                        span.onclick = () => showWordDetails(w, span);
                        
                        cloud.appendChild(span);
                    });
                }
                
                results.style.display = 'block';
            } catch (e) {
                alert('Analysis failed: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.innerText = 'Analyze';
            }
        }

        function showWordDetails(word, el) {
            document.querySelectorAll('.cloud-word').forEach(s => s.classList.remove('active'));
            el.classList.add('active');
            
            const results = currentMemberships.filter(m => 
                m.name.toLowerCase().includes(word.toLowerCase())
            );
            
            const details = document.getElementById('prof_details');
            details.style.display = 'block';
            
            details.innerHTML = `
                <div class="word-tag"># ${word}</div>
                <div style="font-size: 13px; color: var(--text-dim); margin-bottom: 15px; font-weight: 600;">
                    Found in ${results.length} lists:
                </div>
                <div class="prof-detail-card">
                    <table class="prof-table">
                        <thead>
                            <tr>
                                <th>List Name</th>
                                <th>Owner</th>
                                <th style="text-align:right">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${results.map(m => `
                                <tr>
                                    <td style="font-weight:700; color:var(--text)">${m.name}</td>
                                    <td style="color:var(--text-dim)">@${m.owner}</td>
                                    <td style="text-align:right">
                                        <a href="https://x.com/i/lists/${m.id}" target="_blank" class="btn-action" style="padding: 6px 14px; font-size: 11px; display: inline-flex; text-decoration: none;">
                                            VIEW LIST
                                        </a>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
            
            setTimeout(() => {
                details.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 100);
        }

        async function startAnalysis() {
            reportOpened = false;
            lastKnownReport = null;
            await fetch('/api/run', { method: 'POST', body: '{}' });
        }

        function openRankingModal() {
            document.getElementById('rankingModal').style.display = 'flex';
        }
        function closeRankingModal() {
            document.getElementById('rankingModal').style.display = 'none';
        }

        function toggleMethodology(show, targetId) {
            const sec = document.getElementById('methodology_sec');
            const btn = document.getElementById('meth_toggle_btn');
            
            // If called without arguments, toggle current state
            const shouldShow = (show !== undefined) ? show : (sec.style.display === 'none');
            
            if (shouldShow) {
                sec.style.display = 'block';
                btn.innerHTML = '🧠 Hide Methodology';
                setTimeout(() => {
                    const scrollTarget = targetId ? document.getElementById(targetId) : sec;
                    scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 100);
            } else {
                sec.style.display = 'none';
                btn.innerHTML = '🧠 View Methodology';
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }

        loadConfig();
        setInterval(poll, 1500);
    </script>

    <!-- Image Modal -->
    <div id="imageModal" class="modal" onclick="closeModal()">
        <span class="close-modal" onclick="closeModal()">&times;</span>
        <img class="modal-content" id="modalImg" onclick="event.stopPropagation()">
    </div>

    <!-- Ranking Modal -->
    <div id="rankingModal" class="modal" onclick="closeRankingModal()">
        <span class="close-modal" onclick="closeRankingModal()">&times;</span>
        <div class="modal-content card" style="max-width: 800px; cursor: default; padding: 40px;" onclick="event.stopPropagation()">
            <h2 style="margin-top: 0; font-size: 28px; font-weight: 800; border-bottom: 1px solid var(--border); padding-bottom: 20px;">
                Intelligence Provider Ranking
            </h2>
            <p style="color: var(--text-dim); font-size: 14px; line-height: 1.6; margin-bottom: 25px;">
                Based on latency, context window size, and instruction-following for summarization tasks.
            </p>
            <table class="rank-table">
                <thead>
                    <tr>
                        <th style="width: 60px;">Rank</th>
                        <th>Provider</th>
                        <th>Why it belongs here</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td class="rank-num">1</td>
                        <td><strong>Groq</strong><br><span style="font-size:11px; color:var(--text-dim);">Llama 3.3 70B</span></td>
                        <td><strong>Speed King.</strong> Near-instant reporting. Best for quick summaries.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">2</td>
                        <td><strong>Gemini</strong><br><span style="font-size:11px; color:var(--text-dim);">1.5 Flash</span></td>
                        <td><strong>Context King.</strong> 1.5M token window. Can summarize 1,000+ tweets without truncation.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">3</td>
                        <td><strong>Claude</strong><br><span style="font-size:11px; color:var(--text-dim);">3.5 Sonnet</span></td>
                        <td><strong>Writing Quality.</strong> Best synthesis and capture of conversational nuance.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">4</td>
                        <td><strong>Grok</strong><br><span style="font-size:11px; color:var(--text-dim);">Grok-3</span></td>
                        <td><strong>The Super-Model.</strong> Deeply integrated with X content. Unrivaled reasoning and freshness.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">5</td>
                        <td><strong>DeepSeek</strong><br><span style="font-size:11px; color:var(--text-dim);">V3 (Chat)</span></td>
                        <td><strong>Efficiency Expert.</strong> Matches GPT-4o intelligence at 1/10th the cost.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">5</td>
                        <td><strong>OpenAI</strong><br><span style="font-size:11px; color:var(--text-dim);">GPT-4o</span></td>
                        <td><strong>The Reliability Go-to.</strong> Strong reasoning, widely supported.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">6</td>
                        <td><strong>OpenRouter</strong><br><span style="font-size:11px; color:var(--text-dim);">All Models</span></td>
                        <td><strong>The Safety Net.</strong> Access any model instantly without code changes.</td>
                    </tr>
                    <tr>
                        <td class="rank-num">7</td>
                        <td><strong>Local</strong><br><span style="font-size:11px; color:var(--text-dim);">Ollama/LMStudio</span></td>
                        <td><strong>Privacy First.</strong> Zero data leaves your machine. Slower but secure.</td>
                    </tr>
                </tbody>
            </table>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-top: 30px;">
                <button class="run-btn" style="background: #1d9bf020; border: 1px solid #1d9bf040;" onclick="closeRankingModal(); toggleMethodology(true, 'meth_2')">
                    🧠 View Scoring Logic
                </button>
                <button class="run-btn btn-full" onclick="closeRankingModal()">Got it</button>
            </div>
        </div>
    </div>
</body>
</html>'''

def run_server(app_state):
    handler = lambda *args, **kwargs: DashHandler(*args, app_state=app_state, **kwargs)
    with ThreadingHTTPServer(("", PORT), handler) as httpd:
        print(f"🚀 Dashboard running at http://localhost:{PORT}")
        webbrowser.open(f"http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    app_state = {'running': False, 'status_msg': '', 'progress': 0, 'error': None, 'last_report': None}
    Path('logs').mkdir(exist_ok=True)
    run_server(app_state)

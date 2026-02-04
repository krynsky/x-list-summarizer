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

    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(self.get_reconstructed_html().encode())
            
        elif parsed.path == '/api/status':
            now = time.time()
            cache_limit = 60 # 1 minute
            
            # 1. X Auth Verification (Cached)
            if not hasattr(DashHandler, '_x_cache') or (now - getattr(DashHandler, '_x_cache_time', 0) > cache_limit):
                x_status = {'active': False, 'message': 'Not logged in'}
                if COOKIES_PATH.exists():
                    try:
                        fetcher = XListFetcher()
                        loop = asyncio.new_event_loop()
                        success, msg = loop.run_until_complete(fetcher.verify_session())
                        x_status = {'active': success, 'message': msg}
                        loop.close()
                    except Exception as e: x_status = {'active': False, 'message': 'Auth Error'}
                DashHandler._x_cache = x_status
                DashHandler._x_cache_time = now
            else: x_status = DashHandler._x_cache
            
            # 2. AI Verification (Cached)
            if not hasattr(DashHandler, '_ai_cache') or (now - getattr(DashHandler, '_ai_cache_time', 0) > cache_limit):
                ai_status = {'active': False, 'message': 'Checking...'}
                try:
                    config = self.load_config()
                    provider = LLMProvider(config)
                    ai_status = provider.verify()
                    # Keep the detailed message from verify() if it's active
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
                    # Use explorer.exe directly to ensure it pops to front
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

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get('Content-Length', 0))
        data = json.loads(self.rfile.read(length).decode())
        
        if parsed.path == '/api/save-config':
            self.save_config(data)
            # Invalidate AI cache to force immediate re-verification
            if hasattr(DashHandler, '_ai_cache_time'):
                DashHandler._ai_cache_time = 0
            self.send_json({'success': True})

        elif parsed.path == '/api/save-cookies':
            COOKIES_PATH.parent.mkdir(exist_ok=True)
            with open(COOKIES_PATH, 'w') as f: json.dump(data, f)
            # Invalidate X cache to force immediate re-verification
            if hasattr(DashHandler, '_x_cache_time'):
                DashHandler._x_cache_time = 0
            self.send_json({'success': True})

        elif parsed.path == '/api/run':
            if not self.app_state.get('running'):
                self.app_state.update({'running': True, 'progress': 0, 'status_msg': 'Starting...', 'error': None, 'last_report': None})
                threading.Thread(target=self.run_task).start()
                self.send_json({'success': True})
            else:
                self.send_json({'success': False, 'error': 'Already running'})

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
            
            urls = config['twitter'].get('list_urls', [])
            max_t = config['twitter'].get('max_tweets', 100)
            
            all_tweets = []
            async def fetch_and_update(url, i):
                return await fetcher.fetch_list_tweets(url, max_t)

            self.app_state['status_msg'] = f"Fetching {len(urls)} lists in parallel..."
            print(f"📥 [Performance] fetching {len(urls)} lists...")
            t1 = time.time()
            tasks = [fetch_and_update(url, i) for i, url in enumerate(urls)]
            results = await asyncio.gather(*tasks)
            for r in results: all_tweets.extend(r)
            print(f"📥 [Performance] fetching took {time.time()-t1:.2f}s ({len(all_tweets)} tweets total)")
            
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
            fetcher.generate_html_report(agg, summary, OUTPUT_DIR / fname, tweet_count=len(all_tweets))
            
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
            display: flex; align-items: center; justify-content: flex-start;
            padding: 0 40px; height: 90px;
            background: var(--header); border-bottom: 1px solid var(--border);
            position: sticky; top: 0; z-index: 100;
        }
        .main-nav {
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            gap: 20px;
            white-space: nowrap;
        }
        .logo-area { display: flex; align-items: center; gap: 14px; cursor: pointer; }
        .logo-box { 
            width: 80px; height: 80px; border-radius: 20px; 
            background: url("icon.png") center/cover;
            box-shadow: 0 0 30px rgba(29, 155, 240, 0.25);
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

        .nav-links { display: flex; align-items: center; gap: 15px; flex-shrink: 0; }
        .nav-link { 
            color: var(--text-dim); text-decoration: none; font-weight: 700; font-size: 14px; 
            cursor: pointer; transition: 0.2s;
            display: flex; align-items: center;
            padding: 10px 20px; border-radius: 12px;
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

        /* Tab Content */
        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeIn 0.3s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <header>
        <div class="main-nav">
            <div class="middle-section">
                <div class="logo-area" onclick="resetApp()" style="margin-right: 15px;">
                    <div class="logo-box"></div>
                </div>
                <div class="status-container">
                    <span class="status-label" id="run-status">Ready</span>
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
                <a class="nav-link" id="nav-guide" onclick="showTab('guide')">Logic</a>
                <a class="nav-link" id="nav-report" onclick="viewLatest()">View Report</a>
                <a class="nav-link" id="nav-history" onclick="showTab('history')">History</a>
                <a class="nav-link" id="nav-settings" onclick="showTab('settings')">Settings</a>
            </div>
        </div>
    </header>

    <div id="home" class="container tab-content active">
        <div style="text-align:center; margin: 60px 0 80px;">
            <h1 style="font-size: 52px; font-weight: 800; margin-bottom: 25px;">X List Summarizer <span style="font-size: 18px; opacity: 0.6; font-weight: 600; margin-left: 10px;">v1.0</span></h1>
            <p style="font-size: 18px; color: var(--text-dim); line-height: 1.6; max-width: 650px; margin: 0 auto;">Turn the noise of X into actionable intelligence. This premium tool analyzes curated lists to extract high-signal trends and media.</p>
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px;">
            <div class="card" style="padding: 35px; border-radius: 28px;">
                <span style="font-size: 36px; display: block; margin-bottom: 20px;">🔍</span>
                <div style="font-weight: 800; font-size: 19px; margin-bottom: 12px;">Smart Fetching</div>
                <div style="font-size: 14px; color: var(--text-dim); line-height: 1.6;">Automatically dives into your configured X Lists to retrieve the latest posts, including high-resolution images and videos.</div>
            </div>
            <div class="card" style="padding: 35px; border-radius: 28px;">
                <span style="font-size: 36px; display: block; margin-bottom: 20px;">📈</span>
                <div style="font-weight: 800; font-size: 19px; margin-bottom: 12px;">Engagement Ranking</div>
                <div style="font-size: 14px; color: var(--text-dim); line-height: 1.6;">Identifies trending external links by calculating a custom engagement score (Likes + RTs + Replies + Bookmarks).</div>
            </div>
            <div class="card" style="padding: 35px; border-radius: 28px;">
                <span style="font-size: 36px; display: block; margin-bottom: 20px;">🤖</span>
                <div style="font-weight: 800; font-size: 19px; margin-bottom: 12px;">AI Synthesis</div>
                <div style="font-size: 14px; color: var(--text-dim); line-height: 1.6;">Uses advanced LLMs to read through hundreds of posts and synthesize a structured executive summary of the discussion.</div>
            </div>
        </div>
        <div class="card" style="text-align: center; background: linear-gradient(135deg, rgba(29,155,240,0.06), rgba(29,155,240,0.02)); border: 1px solid rgba(29,155,240,0.15); margin-top: 40px; padding: 45px;">
            <div style="font-weight: 800; font-size: 20px; margin-bottom: 15px;">Ready to begin?</div>
            <div style="font-size: 15px; color: var(--text-dim);">Ensure your <strong>X Authentication</strong> and <strong>AI Model</strong> are configured in Settings, then click <strong>Run Analysis</strong> in the header to start.</div>
        </div>
    </div>

    <div id="settings" class="container tab-content">
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
                    <div class="sec-title">🤖 AI Intelligence</div>
                    <label>Provider</label>
                    <select id="s_prov" onchange="renderProviderOptions()">
                        <option value="groq">Groq (Free Cloud)</option>
                        <option value="ollama">Ollama (Local)</option>
                        <option value="lmstudio">LM Studio (Local)</option>
                        <option value="claude">Anthropic Claude</option>
                        <option value="openai">OpenAI GPT-4o</option>
                    </select>

                    <label>Model Name</label>
                    <input type="text" id="p_mod" placeholder="openai/gpt-oss-120b">

                    <label>API Key</label>
                    <input type="password" id="p_key" placeholder="••••••••••••••••••••••••••••••••••••••••••••••••">

                    <button class="run-btn btn-full btn-save" onclick="saveConfig()">
                        <span>💾</span> Save App Configuration
                    </button>
                </div>
            </div>

            <div class="right-col">
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
                    </div>

                    <button class="run-btn btn-full" onclick="saveCookies()">
                        <span>🔑</span> Update Session Cookies
                    </button>
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

    <div id="guide" class="container tab-content">
        <div style="max-width: 850px; margin: 40px auto;">
            <div style="text-align: center; margin-bottom: 60px;">
                <h1 style="font-size: 42px; font-weight: 800; margin-bottom: 20px;">Logic & Methodology</h1>
                <p style="color: var(--text-dim); font-size: 16px;">Understanding how the X List Summarizer processes your data.</p>
            </div>
            
            <div class="card" style="margin-bottom: 30px; padding: 40px; border-radius: 24px;">
                <div style="font-size: 24px; font-weight: 800; margin-bottom: 20px; color: var(--accent); display: flex; align-items: center; gap: 15px;">
                    <span>📊</span> 1. Quantity & Fetching
                </div>
                <p style="color: var(--text-dim); line-height: 1.7; font-size: 15px;">The app follows a <strong>"Latest-First"</strong> approach. It fetches a specific number of tweets per list based on your configurations.</p>
                <ul class="guide-list">
                    <li><strong>Per-List Limit:</strong> Managed via the "Max Tweets" setting (Default: 100).</li>
                    <li><strong>Total Scope:</strong> If you have 5 lists and a limit of 100, the system analyzes 500 total tweets to find the highest signal.</li>
                    <li><strong>Deduplication:</strong> If the same tweet appears in multiple lists, it is only counted once for engagement.</li>
                </ul>
            </div>

            <div class="card" style="margin-bottom: 30px; padding: 40px; border-radius: 24px;">
                <div style="font-size: 24px; font-weight: 800; margin-bottom: 20px; color: var(--accent); display: flex; align-items: center; gap: 15px;">
                    <span>⏳</span> 2. Timeframe Analysis
                </div>
                <p style="color: var(--text-dim); line-height: 1.7; font-size: 15px;">The system does not use a rigid calendar cut-off (like "last 24 hours"). Instead, it prioritizes <strong>recency and depth</strong>.</p>
                <ul class="guide-list">
                    <li><strong>Dynamic Range:</strong> On busy lists with 50+ members, 100 tweets might cover the last 2 hours. On smaller, niche lists, the same 100 tweets could span several days.</li>
                    <li><strong>The Logic:</strong> We grab the very newest content and move backward through history until your "Max Tweets" quota is filled.</li>
                </ul>
            </div>

            <div class="card" style="margin-bottom: 30px; padding: 40px; border-radius: 24px;">
                <div style="font-size: 24px; font-weight: 800; margin-bottom: 20px; color: var(--accent); display: flex; align-items: center; gap: 15px;">
                    <span>🧠</span> 3. Engagement Ranking Logic
                </div>
                <p style="color: var(--text-dim); line-height: 1.7; font-size: 15px;">Low-signal "noise" is filtered out using a weighted scoring algorithm before the AI even sees the data:</p>
                <ul class="guide-list">
                    <li><strong>Link Aggregation:</strong> We identify external URLs and group all tweets that shared or discussed that specific link.</li>
                    <li><strong>Weighted Scoring:</strong> Every link's "Power Score" is calculated based on: <strong>Likes + (Retweets * 1.5) + (Replies * 2) + Bookmarks</strong>.</li>
                    <li><strong>Filtering:</strong> The top 30 link-groups are displayed in your report. For AI synthesis, the system focuses on the <strong>top 20</strong> to ensure high precision and stay within model limits.</li>
                </ul>
            </div>

            <div class="card" style="margin-bottom: 30px; padding: 40px; border-radius: 24px;">
                <div style="font-size: 24px; font-weight: 800; margin-bottom: 20px; color: var(--accent); display: flex; align-items: center; gap: 15px;">
                    <span>🤖</span> 4. AI Synthesis & Report Sections
                </div>
                <p style="color: var(--text-dim); line-height: 1.7; font-size: 15px;">The final report is structured into three logical sections, each using a specific extraction algorithm:</p>
                <div style="margin-top: 20px;">
                    <div style="margin-bottom: 20px;">
                        <strong style="color:var(--text); display:block; margin-bottom:5px;">Section A: TL;DR Table</strong>
                        <div style="font-size: 14px; color: var(--text-dim);">Uses a "Key-Impact" extractor to identify the most significant items mentioned and explains <em>why</em> they matter to the community right now.</div>
                    </div>
                    <div style="margin-bottom: 20px;">
                        <strong style="color:var(--text); display:block; margin-bottom:5px;">Section B: Main Topics & Themes</strong>
                        <div style="font-size: 14px; color: var(--text-dim);">A synthetic layer that groups hundreds of individual tweets into cohesive narrative themes, providing a numbered "Executive Summary" of the broader discussion.</div>
                    </div>
                    <div>
                        <strong style="color:var(--text); display:block; margin-bottom:5px;">Section C: Most Shared Content & Why</strong>
                        <div style="font-size: 14px; color: var(--text-dim);">A data-driven ranker that pulls the top external links, calculates their mention count, and analyzes the sentiment/context for why they are trending.</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div id="report" class="container tab-content" style="max-width: 100%; padding: 0;">
        <iframe id="report-frame" style="width: 100%; height: calc(100vh - 90px); border: none;"></iframe>
    </div>

    <script>
        let cfg = {};
        function showTab(t) {
            document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
            document.querySelectorAll('.nav-link').forEach(x => x.classList.remove('active'));
            const tab = document.getElementById(t);
            if (tab) tab.classList.add('active');
            
            const nav = document.getElementById('nav-' + t);
            if(nav) nav.classList.add('active');
            
            if(t === 'history') loadHistory();
        }

        function resetApp() {
            // Restore home screen and clear report frame
            document.getElementById('report-frame').src = 'about:blank';
            showTab('home');
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

        async function viewLatest() {
            const r = await fetch('/api/status');
            const s = await r.json();
            const reportName = s.last_report || 'latest';
            loadInAppReport(reportName);
            document.getElementById('progress-overlay').style.display = 'none';
        }

        function loadInAppReport(name) {
            const frame = document.getElementById('report-frame');
            frame.src = '/output/' + name;
            showTab('report');
        }

        async function loadConfig() {
            const r = await fetch('/api/config');
            cfg = await r.json();
            document.getElementById('s_urls').value = (cfg.twitter.list_urls || []).join('\\n');
            document.getElementById('s_max').value = cfg.twitter.max_tweets;
            document.getElementById('s_prov').value = cfg.summarization.provider;
            document.getElementById('s_owner').value = cfg.twitter.list_owner || '';
            renderProviderOptions();
        }

        function renderProviderOptions() {
            const p = document.getElementById('s_prov').value;
            const data = cfg.summarization.options[p] || {};
            if(document.getElementById('p_mod')) document.getElementById('p_mod').value = data.model || '';
            if(document.getElementById('p_key')) document.getElementById('p_key').value = data.api_key || '';
        }

        async function saveConfig() {
            const p = document.getElementById('s_prov').value;
            const newCfg = { ...cfg };
            newCfg.summarization.provider = p;
            newCfg.twitter.list_urls = document.getElementById('s_urls').value.split('\\n').filter(x => x.trim());
            newCfg.twitter.max_tweets = parseInt(document.getElementById('s_max').value);
            newCfg.twitter.list_owner = document.getElementById('s_owner').value || null;
            newCfg.summarization.options[p].model = document.getElementById('p_mod').value;
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

        async function openFolder() {
            await fetch('/api/open-folder');
        }

        async function startAnalysis() {
            reportOpened = false;
            lastKnownReport = null;
            await fetch('/api/run', { method: 'POST', body: '{}' });
        }

        loadConfig();
        setInterval(poll, 1500);
    </script>
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

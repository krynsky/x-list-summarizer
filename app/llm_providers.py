#!/usr/bin/env python3
"""
LLM Provider abstraction layer.
Supports multiple backends: Ollama, Claude, OpenAI, LM Studio, etc.
"""

import requests
import time
from anthropic import Anthropic
from openai import OpenAI

class LLMProvider:
    """Abstraction layer for different LLM backends."""
    
    def __init__(self, config):
        """
        Initialize LLM provider.
        
        Args:
            config: Configuration dictionary with 'summarization' section
        """
        self.provider = config['summarization']['provider']
        self.config = config['summarization']['options'][self.provider]

    def _get_effective_config(self):
        """Helper to get resolved endpoint, key and model based on provider."""
        cfg = self.config.copy()
        endpoint = cfg.get('endpoint', '')
        api_key = cfg.get('api_key', 'not-needed')
        model = cfg.get('model', 'local-model')

        if self.provider == 'openai':
            endpoint = 'https://api.openai.com/v1'
        elif self.provider == 'groq':
            if not endpoint or 'api.openai.com' in endpoint:
                endpoint = 'https://api.groq.com/openai/v1'
        elif self.provider == 'gemini':
            if not endpoint: endpoint = 'https://generativelanguage.googleapis.com/v1beta/openai/'
        elif self.provider == 'deepseek':
            if not endpoint: endpoint = 'https://api.deepseek.com'
        elif self.provider == 'openrouter':
            if not endpoint: endpoint = 'https://openrouter.ai/api/v1'
        elif self.provider == 'grok':
            if not endpoint: endpoint = 'https://api.x.ai/v1'
        elif self.provider == 'claude':
            model = cfg.get('model', 'claude-3-5-sonnet-20240620')
        elif not endpoint:
            # Local defaults
            if self.provider == 'ollama': endpoint = 'http://localhost:11434'
            elif self.provider == 'lmstudio': endpoint = 'http://localhost:1234/v1'
            else: endpoint = 'http://localhost:8000/v1'
            
        return endpoint, api_key, model

    def verify(self):
        """
        Verify the connection and model functionality of the LLM provider.
        """
        try:
            endpoint, api_key, model = self._get_effective_config()
            
            if self.provider == 'ollama':
                try:
                    # Check connection
                    requests.get(f"{endpoint}/api/tags", timeout=5)
                    # Check model exists (via tiny prompt)
                    requests.post(f"{endpoint}/api/generate", json={
                        "model": model, "prompt": "hi", "max_tokens": 1, "stream": False
                    }, timeout=5.0)
                    return {'active': True, 'message': f'Ready ({model})'}
                except Exception as e:
                    return {'active': False, 'message': f'Ollama Error: Check if {model} is pulled.'}
            
            elif self.provider in ['lmstudio', 'vllm', 'generic_openai', 'groq', 'openai', 'gemini', 'deepseek', 'openrouter', 'grok']:
                try:
                    client = OpenAI(base_url=endpoint, api_key=api_key, timeout=10.0)

                    if self.provider == 'gemini':
                        # Gemini free tier has very low chat RPM — use the cheap model-list
                        # endpoint to verify the key is valid without burning a chat quota slot.
                        available = [m.id for m in client.models.list()]
                        # Model IDs come back as 'models/gemini-2.5-flash' — strip prefix
                        bare = [m.split('/')[-1] for m in available]
                        if model in bare or model in available:
                            return {'active': True, 'message': f'Ready ({model})'}
                        else:
                            return {'active': False, 'message': f'Model {model} not found'}
                    else:
                        # Tiny completion health check for all other providers
                        client.chat.completions.create(
                            model=model,
                            messages=[{"role": "user", "content": "hi"}],
                            max_tokens=1
                        )
                        return {'active': True, 'message': f'Ready ({model})'}
                except Exception as e:
                    err = str(e).lower()
                    if '401' in err or 'api key' in err: return {'active': False, 'message': 'Invalid API Key'}
                    if 'rate_limit' in err or '429' in err: return {'active': False, 'message': 'Rate limited — key is valid, try again shortly'}
                    if '404' in err or 'not found' in err: return {'active': False, 'message': f'Model {model} not found'}
                    return {'active': False, 'message': f'Connection failed: {str(e)[:50]}...'}

            elif self.provider == 'claude':
                if not api_key: return {'active': False, 'message': 'Missing API Key'}
                return {'active': True, 'message': f'Ready ({model})'}
                
            return {'active': True, 'message': 'Provider check skipped'}
            
        except Exception as e:
            return {'active': False, 'message': f"Unexpected Error: {str(e)[:60]}"}

    def summarize(self, aggregated_data):
        """
        Summarize aggregated tweet data.
        
        Args:
            aggregated_data: Dictionary with 'by_link' and 'no_links' keys
            
        Returns:
            Summary text
        """
        # Build prompt from aggregated data
        prompt = self._build_prompt(aggregated_data)
        
        # Route to appropriate provider
        if self.provider == 'ollama':
            return self._ollama(prompt)
        elif self.provider == 'claude':
            return self._claude(prompt)
        elif self.provider == 'openai':
            return self._openai(prompt)
        elif self.provider in ['lmstudio', 'vllm', 'generic_openai', 'groq', 'gemini', 'deepseek', 'openrouter', 'grok']:
            return self._openai_compatible(prompt)
        elif self.provider == 'llamacpp':
            return self._llamacpp(prompt)
        elif self.provider == 'koboldai':
            return self._koboldai(prompt)
        elif self.provider == 'textgenwebui':
            return self._textgen_webui(prompt)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")
    
    def _build_prompt(self, aggregated_data):
        """Build summarization prompt from aggregated data."""
        prompt_parts = []
        
        prompt_parts.append("You are analyzing tweets from an X/Twitter list. For each shared link listed below, write 1-2 sentences explaining why it's being shared and the overall sentiment expressed by the tweeters.")
        prompt_parts.append("Output EXACTLY ONE LINE per link. No headers, no bullet points, no numbered lists, no extra text.\nFormat: domain.com :: Your 1-2 sentence explanation of why it's trending and the sentiment\n")
        
        links = aggregated_data['by_link']
        if links:
            sorted_links = sorted(links, key=lambda x: len(x[1]), reverse=True)[:20]
            prompt_parts.append("TOP SHARED LINKS (with sample tweets for context):")
            for link, tweets in sorted_links:
                try:
                    from urllib.parse import urlparse as _up
                    domain = _up(link).netloc.replace('www.', '') if link.startswith('http') else link
                except:
                    domain = link[:60]
                prompt_parts.append(f"\n[{domain}] — {len(tweets)} tweets")
                for tweet in tweets[:3]:
                    txt = tweet['text'][:200].replace('\n', ' ')
                    prompt_parts.append(f"  @{tweet['author']}: {txt}")
        
        if aggregated_data['no_links']:
            prompt_parts.append("\n\nOTHER CONTEXT (tweets without shared links):")
            for tweet in aggregated_data['no_links'][:5]:
                txt = tweet['text'][:150].replace('\n', ' ')
                prompt_parts.append(f"  @{tweet['author']}: {txt}")
        
        prompt = "\n".join(prompt_parts)
        
        if len(prompt) > 30000:
            print(f"⚠️ Prompt too large ({len(prompt)}), truncating...")
            prompt = prompt[:30000] + "\n\n[TRUNCATED DUE TO SIZE]"

        # Groq has tighter per-minute token limits — cap prompt smaller to stay safe
        if getattr(self, 'provider', '') == 'groq' and len(prompt) > 15000:
            print(f"⚠️ Groq prompt large ({len(prompt)}), trimming to 15K...")
            prompt = prompt[:15000] + "\n\n[TRUNCATED FOR GROQ LIMIT]"
            
        print(f"📝 Prompt built: {len(prompt)} characters")
        return prompt
    
    def _ollama(self, prompt):
        """Ollama backend."""
        endpoint, _, model = self._get_effective_config()
        
        try:
            response = requests.post(
                f"{endpoint}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json()['response']
        except Exception as e:
            return f"Error with Ollama: {e}\n\nPlease check that Ollama is running and the model '{model}' is installed."
    
    def _claude(self, prompt):
        """Claude API backend."""
        _, api_key, model = self._get_effective_config()
        if not api_key:
            return "Error: Claude API key not configured. Please add your API key to config.json"
        
        try:
            client = Anthropic(api_key=api_key)
            message = client.messages.create(
                model=model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            return message.content[0].text
        except Exception as e:
            return f"Error with Claude API: {e}"
    
    def _openai(self, prompt):
        """OpenAI API backend."""
        endpoint, api_key, model = self._get_effective_config()
        if not api_key:
            return "Error: OpenAI API key not configured. Please add your API key to config.json"
        
        try:
            client = OpenAI(base_url=endpoint, api_key=api_key)
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error with OpenAI API: {e}"
    
    def _openai_compatible(self, prompt):
        """OpenAI-compatible backends (LM Studio, vLLM, Groq, Gemini, etc.)."""
        endpoint, api_key, model = self._get_effective_config()
        
        max_attempts = 3
        retry_delays = [15, 30]  # seconds to wait between attempts

        for attempt in range(max_attempts):
            try:
                client = OpenAI(base_url=endpoint, api_key=api_key)
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=2000,
                    timeout=180
                )
                return response.choices[0].message.content

            except Exception as e:
                msg = str(e).lower()
                is_rate_limit = 'rate_limit' in msg or '429' in msg

                if is_rate_limit and attempt < max_attempts - 1:
                    wait = retry_delays[attempt]
                    # Respect Retry-After header if present in the error message
                    import re
                    match = re.search(r'retry.after[^\d]*(\d+)', msg)
                    if match:
                        wait = min(int(match.group(1)) + 2, 60)
                    print(f"⏳ {self.provider} rate limited (attempt {attempt+1}/{max_attempts}), waiting {wait}s...")
                    time.sleep(wait)
                    continue  # retry

                # Final attempt failed or non-retriable error
                print(f"❌ AI Error ({self.provider}): {msg}")
                if is_rate_limit:
                    if self.provider == 'gemini':
                        return f"Error with gemini: Rate limited (free tier has low RPM). Wait 1–2 minutes, reduce Max Tweets in Settings, or switch to Groq."
                    return f"Error with {self.provider}: Rate limited after {max_attempts} attempts. Try reducing Max Tweets in Settings, or wait a minute and retry."
                if 'context_length' in msg or 'maximum context' in msg:
                    return f"Error with {self.provider}: Prompt too large for this model's context window."
                return f"Error with {self.provider}: {str(e)}\n\nPlease check your settings and connection."
    
    def _llamacpp(self, prompt):
        """llama.cpp server backend."""
        endpoint = self.config.get('endpoint', 'http://localhost:8080')
        
        try:
            response = requests.post(
                f"{endpoint}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 2000,
                    "temperature": 0.7
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json()['content']
        except Exception as e:
            return f"Error with llama.cpp: {e}\n\nPlease check that llama.cpp server is running at {endpoint}"
    
    def _koboldai(self, prompt):
        """KoboldAI backend."""
        endpoint = self.config.get('endpoint', 'http://localhost:5001')
        
        try:
            response = requests.post(
                f"{endpoint}/api/v1/generate",
                json={
                    "prompt": prompt,
                    "max_length": 2000,
                    "temperature": 0.7
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json()['results'][0]['text']
        except Exception as e:
            return f"Error with KoboldAI: {e}\n\nPlease check that KoboldAI is running at {endpoint}"
    
    def _textgen_webui(self, prompt):
        """Text Generation WebUI backend."""
        endpoint = self.config.get('endpoint', 'http://localhost:5000')
        
        try:
            response = requests.post(
                f"{endpoint}/api/v1/generate",
                json={
                    "prompt": prompt,
                    "max_tokens": 2000
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json()['results'][0]['text']
        except Exception as e:
            return f"Error with Text Generation WebUI: {e}\n\nPlease check that the server is running at {endpoint}"

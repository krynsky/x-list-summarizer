#!/usr/bin/env python3
"""
LLM Provider abstraction layer.
Supports multiple backends: Ollama, Claude, OpenAI, LM Studio, etc.
"""

import requests
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
            
            elif self.provider in ['lmstudio', 'vllm', 'generic_openai', 'groq', 'openai']:
                try:
                    client = OpenAI(base_url=endpoint, api_key=api_key, timeout=10.0)
                    # Tiny completion health check
                    client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "hi"}],
                        max_tokens=1
                    )
                    return {'active': True, 'message': f'Ready ({model})'}
                except Exception as e:
                    err = str(e).lower()
                    if '401' in err or 'api key' in err: return {'active': False, 'message': 'Invalid API Key'}
                    if '404' in err or 'model' in err: return {'active': False, 'message': f'Model {model} not found'}
                    if 'rate_limit' in err or '429' in err: return {'active': False, 'message': 'Rate limited (429)'}
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
        elif self.provider in ['lmstudio', 'vllm', 'generic_openai', 'groq']:
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
        
        prompt_parts.append("Analyze and summarize the following tweets from an X/Twitter list.")
        prompt_parts.append("Focus on the main themes, discussions, and shared content.\n")
        
        # 1. Sort and Limit Links (Top 20 by engagement)
        links = aggregated_data['by_link']
        if links:
            links = sorted(links, key=lambda x: len(x[1]), reverse=True)[:20]
            prompt_parts.append("TWEETS GROUPED BY SHARED LINKS (Top 20):")
            for link, tweets in links: 
                prompt_parts.append(f"\nLink: {link}")
                prompt_parts.append(f"({len(tweets)} tweets about this link)")
                for tweet in tweets[:5]:  # Limit to first 5 tweets per link
                    txt = tweet['text'][:200].replace('\n', ' ')
                    prompt_parts.append(f"  - @{tweet['author']}: {txt}")
        
        # 2. Add individual tweets (Limit to 10)
        if aggregated_data['no_links']:
            prompt_parts.append("\n\nOTHER TWEETS:")
            for tweet in aggregated_data['no_links'][:10]: 
                txt = tweet['text'][:200].replace('\n', ' ')
                prompt_parts.append(f"  - @{tweet['author']}: {txt}")
        
        prompt_parts.append("\nStrictly provide the summary in the following structure for premium reporting. Important: Use ' :: ' (space-colon-colon-space) as the separator for table rows. Do NOT use markdown table syntax like '| --- |'.")
        
        prompt_parts.append("\n### TL;DR - What the list is talking about")
        prompt_parts.append("Format: Group Name - Item 1, Item 2 :: Detailed synthesis of significance. (Do NOT include prefixes like 'X/Twitter List - ' in the Group Name. Provide 5-8 rows)")
        
        prompt_parts.append("\n### 1. Main Topics & Themes")
        prompt_parts.append("Format: 1. Theme Name – Detailed synthesis. (Use the '–' separator)")
        
        prompt_parts.append("\n### 2. Most Shared Content & Why")
        prompt_parts.append("Format: Content Title (or Domain) :: Mention count (e.g. 10 tweets) :: Why it's trending and sentiment. (Exactly 3 parts separated by ' :: ')")
        
        prompt = "\n".join(prompt_parts)
        
        # 3. Final Truncation Safety (Limit to ~30k chars)
        if len(prompt) > 30000:
            print(f"⚠️ Prompt too large ({len(prompt)}), truncating...")
            prompt = prompt[:30000] + "\n\n[TRUNCATED DUE TO SIZE]"
            
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
        """OpenAI-compatible backends (LM Studio, vLLM, etc.)."""
        endpoint, api_key, model = self._get_effective_config()
        
        try:
            client = OpenAI(
                base_url=endpoint,
                api_key=api_key
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                timeout=180 # Increased for Groq big summaries
            )
            return response.choices[0].message.content
        except Exception as e:
            msg = str(e).lower()
            print(f"❌ AI Error ({self.provider}): {msg}")
            
            # More helpful messages for common Groq errors
            if 'rate_limit' in msg or '429' in msg:
                return f"Error with {self.provider}: Rate limited. Please wait a minute or use a smaller list."
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

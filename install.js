module.exports = {
    run: [
        {
            method: "shell.run",
            params: {
                message: "python -m venv venv"
            }
        },
        {
            method: "shell.run",
            params: {
                venv: "venv",
                message: "pip install uv"
            }
        },
        {
            method: "shell.run",
            params: {
                venv: "venv",
                message: "uv pip install -r requirements.txt"
            }
        },
        {
            method: "fs.write",
            params: {
                path: "config.json",
                json2: {
                    "summarization": {
                        "provider": "ollama",
                        "options": {
                            "ollama": {
                                "model": "qwen2.5:7b",
                                "endpoint": "http://localhost:11434"
                            },
                            "lmstudio": {
                                "model": "local-model",
                                "endpoint": "http://localhost:1234/v1"
                            },
                            "groq": {
                                "model": "llama-3.3-70b-versatile",
                                "endpoint": "https://api.groq.com/openai/v1",
                                "api_key": ""
                            },
                            "claude": {
                                "model": "claude-3-5-sonnet-20240620",
                                "api_key": ""
                            },
                            "openai": {
                                "model": "gpt-4o",
                                "api_key": ""
                            }
                        }
                    },
                    "twitter": {
                        "list_urls": [],
                        "max_tweets": 100,
                        "list_owner": null,
                        "max_scrolls": 5,
                        "headless_after_auth": true
                    }
                }
            }
        },
        {
            method: "fs.make",
            params: {
                path: "output"
            }
        },
        {
            method: "fs.make",
            params: {
                path: "browser_session"
            }
        },
        {
            method: "notify",
            params: {
                html: "✅ Installation complete! Next step: Login to X"
            }
        }
    ]
}

module.exports = {
    run: [
        {
            method: "shell.run",
            params: {
                message: "git fetch origin && git merge origin/main --no-edit"
            }
        },
        {
            method: "shell.run",
            params: {
                venv: "venv",
                message: "pip install uv && uv pip install -r requirements.txt"
            }
        },
        {
            method: "shell.run",
            params: {
                venv: "venv",
                message: "python apply_twikit_patches.py"
            }
        },
        {
            method: "notify",
            params: {
                html: "✅ Update complete!"
            }
        }
    ]
}

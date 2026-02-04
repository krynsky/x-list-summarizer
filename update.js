module.exports = {
    run: [
        {
            method: "shell.run",
            params: {
                message: "git pull"
            }
        },
        {
            method: "shell.run",
            params: {
                venv: "venv",
                message: "pip install -r requirements.txt"
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

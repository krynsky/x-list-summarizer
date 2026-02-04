module.exports = {
    run: [
        {
            method: "fs.rm",
            params: {
                path: "venv"
            }
        },
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
                message: "pip install -r requirements.txt"
            }
        },
        {
            method: "notify",
            params: {
                html: "✅ Environment reset complete!"
            }
        }
    ]
}

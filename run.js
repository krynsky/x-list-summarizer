module.exports = {
    daemon: true,
    run: [
        {
            method: "shell.run",
            params: {
                venv: "venv",
                message: "python -u app/web_ui.py",
                on: [{
                    "event": "/http:\\/\\/localhost:(\\d+)/",
                    "done": true
                }]
            }
        },
        {
            method: "local.set",
            params: {
                url: "{{input.event[0]}}"
            }
        }
    ]
}

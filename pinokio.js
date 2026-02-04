module.exports = {
    title: "X List Summarizer",
    description: "Unified Dashboard for X List Analysis",
    icon: "icon.png",
    menu: async (kernel) => {
        let installed = await kernel.exists(__dirname, "venv")
        let hasSession = await kernel.exists(__dirname, "browser_session", "cookies.json")

        let items = []

        if (!installed) {
            items.push({
                icon: "fa-solid fa-download",
                text: "Install",
                href: "install.js"
            })
        } else {
            items.push({
                icon: "fa-solid fa-gauge-high",
                text: "Open Dashboard",
                href: "run.js"
            })
        }

        items.push({
            icon: "fa-solid fa-rotate",
            text: "Update",
            href: "update.js"
        })

        items.push({
            icon: "fa-solid fa-plug",
            text: "Reset Environment",
            href: "reset.js",
            confirm: "This will delete the virtual environment and re-install all dependencies. Are you sure?"
        })

        return items
    }
}

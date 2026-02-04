# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-02-04

### Added
- **Dynamic AI Provider Guides**: Integrated contextual help links and setup instructions for Groq, Claude, OpenAI, Ollama, and LM Studio directly into the Settings page.
- **Improved Model Selection**: Replaced the text-based model name input with a provider-aware dropdown containing recommended presets (e.g., Llama 3.3, GPT-4o).
- **Custom Model Fallback**: Added a "Custom..." option to the model selector for manual entry of specific model strings.
- **X Auth Visual Guide**: Added a step-by-step screenshot and instructions within the dashboard to help users find their `auth_token` and `ct0` session cookies.
- **High-Resolution Modals**: Implemented a clickable modal system for instruction images, allowing users to zoom in on complex visuals with a glassmorphism backdrop.
- **Maintenance Scripts**: Added "Update" and "Reset Environment" options to the Pinokio menu for easier dependency management.
- **Security Audit**: Completely sanitized the repository for public release, refining `.gitignore` to exclude sensitive configuration and session data.

### Changed
- **UI Refinement**: Optimized the Settings layout to dynamically hide unnecessary fields (like API Keys for local providers) to reduce user friction.
- **Responsive Media**: Updated README documentation with a cleaner, 100% width screenshot layout.
- **Unified Workspace**: Reconfigured the project for a professional development flow, separating the live production environment from an external D-drive source-of-truth.

### Fixed
- **Installation Reliability**: Fixed virtual environment dependency issues by centralizing installation through a unified symbolic link structure.
- **Version Display**: Standardized versioning across the UI and documentation.

## [1.0.0] - 2026-01-26
- Initial release with Pinokio integration, multi-LLM support, and basic reporting.

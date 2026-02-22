# Changelog

All notable changes to this project will be documented in this file.

## [1.6.0] - 2026-02-22

### Added
- **xAI Grok Integration**: Officially added support for xAI's Grok models (Grok-3, Grok-2, Grok-beta) via a new OpenAI-compatible provider option.
- **Deep Recursive Extraction**: Upgraded the link discovery engine to recursively scan **Retweets and Quote Tweets**. The system now captures high-signal content even when it's shared indirectly.
- **Weighted Power Scoring**: Enhanced the engagement algorithm. Link rankings now factor in **Bookmarks and Quotes** alongside Likes, Retweets, and Replies for a more accurate "pulse" of the community.
- **Report Transparency**: Generated reports now explicitly display the **AI Provider and Model** used for the synthesis, ensuring full traceability of insights.
- **Methodology Deep Dive**: Integrated a comprehensive "Under-the-Hood" section directly into the Settings tab. It provides a collapsible, interactive guide to the app's internal logic, scoring, and media handling.

### Changed
- **Settings UI Revamp**: Reorganized the Settings page to put Methodology at the forefront. Simplified navigation by removing the standalone Logic tab and adding a dynamic "View/Hide Methodology" toggle in the header.
- **Intelligent Media Handling**:
    - **Deduplication**: Implemented cluster-aware deduplication to prevent the same image or video from appearing multiple times in a report's retweet chain.
    - **Reliable Video Access**: Replaced direct video embedding with clickable high-res thumbnails that link to the source tweet, bypassing X's session-based authentication issues for a 100% playback success rate.

### Fixed
- **Twikit Backend Resilience**: Corrected a critical attribute mapping bug where retweets and quotes were not being processed due to library-specific naming changes (`retweeted_tweet` vs `retweeted_status`).
- **X List Metadata**: Improved the accumulation logic for multi-list reports. List Cards now accurately aggregate member counts and list names across all sources.

## [1.5.0] - 2026-02-05

### Added
- **Interactive Profiler**: A powerful new feature to analyze account list memberships.
    - **Dynamic Word Cloud**: Visualizes key themes and interests of an account based on the lists they belong to.
    - **Full Table Interactivity**: Clicking any word in the cloud now zooms into a detailed table of source lists with direct links to X.
- **Account Protection Safegaurds**: Robust security measures to protect users' X accounts.
    - **User ID Caching**: Implemented local storage for resolved IDs to stay well within strict API limits.
    - **Human-Like Staggering**: Added randomized delays and staggered parallel executions to avoid bot detection.
    - **Rate Limit UI Awareness**: The dashboard now specifically detects and explains X Rate Limit (429) errors.

### Performance
- **Turbocharged Installation**: Upgraded the installation and update backends to use `uv` instead of standard `pip`. Dependency resolution and installation are now up to 10x faster.

### Fixed
- **Profiler Backend Stability**: Implemented a manual v1.1 API fallback to resolve memberships that were previously failing due to library limitations.
- **Visual Responsiveness**: Improved word cloud scaling and layout for a more premium Feel.

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

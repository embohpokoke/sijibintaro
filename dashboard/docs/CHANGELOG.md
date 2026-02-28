# SIJI Bintaro CRM — Changelog

## [3.0.0] — 2026-02-28
### Added
- Complete CSS design token system (50+ tokens for colors, spacing, shadows, radius)
- Light/Dark mode toggle with localStorage persistence and prefers-color-scheme fallback
- FOUC prevention (inline script reads theme before page renders)
- Inter font (Google Fonts) for modern typography
- Header bar on each page with page title + controls
- Theme toggle button (sun/moon SVG icons) on Dashboard header
- WhatsApp reply input bar with send button (UI ready, backend integration pending)
- Mobile chat view: full-screen with back button navigation
- Toast notification system
- SVG stroke icons for sidebar and bottom nav (replacing emoji icons)
- Chart theme synchronization (colors update on theme toggle)
- Design System documentation (`docs/DESIGN-SYSTEM.md`)

### Changed
- **Complete visual overhaul** of all components
- **Sidebar:** always-dark anchor regardless of theme, SVG icons, animated pulse dot, 220px width (was 240px)
- **KPI Cards:** gold metric numbers, hover animation (translateY + shadow), subtle borders
- **Status Badges:** pill-style with full border-radius, semantic background colors that adapt to theme
- **Tables:** gold hover on rows, cleaner header typography, variable-based borders
- **Charts:** theme-aware grid/text colors, Inter font labels, updated palette for better contrast
- **WhatsApp Chat:** outgoing bubbles now gold gradient with white text, date separators in pill-style, customer avatars with gold gradient
- **Filter Chips:** cleaner pill style with Inter font
- **Modals:** surface background (was page bg), better shadow depth
- **Typography:** Inter font stack, consistent size scale across all components
- **Mobile:** improved bottom nav with SVG icons, 2-column card grid, chat-open state for WA
- **Colors:** warmer palette — light mode uses cream/off-white (#F5F5F0), dark mode uses deeper blacks (#111)
- **Version:** 2.5 → 3.0 in sidebar footer and settings page

### Fixed
- Inconsistent color usage (25+ hardcoded hex values replaced with CSS variables)
- Chart colors not adapting to background theme
- Missing hover/focus states on interactive elements
- Form inputs now have focus ring (gold glow)
- Badge contrast issues in both light and dark contexts

### Technical
- CSS custom properties architecture for easy theme maintenance
- `ThemeManager` singleton for theme state management
- `chartTheme()` and `updateChartColors()` functions for dynamic chart styling
- DatePicker refactored from class to prototype for consistency
- All inline `style="color:var(--gold)"` patterns updated to use new token names

## [2.5.0] — Feb 2026 (Pre-overhaul)
### Previous State
- Dark-only theme (#0F0F0F background)
- System fonts (no Inter)
- Emoji icons in navigation
- 14 CSS variables (minimal)
- Hardcoded colors throughout CSS and JS
- No theme toggle
- No WA reply input bar
- Functional but utilitarian UI

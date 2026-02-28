# SIJI Bintaro — Design System v1.0

**Created:** 2026-02-28
**Author:** Claude Code (Design Overhaul Phase 2)

## Color Palette

### Brand Colors
| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `--gold-deep` | #B8860B | #B8860B | Accent hover, metric text (light) |
| `--gold` | #D4A017 | #D4A017 | Primary accent, buttons, active states |
| `--gold-light` | #F5C842 | #F5C842 | Highlights, active nav text |
| `--gold-subtle` | rgba(212,160,23,0.08) | rgba(212,160,23,0.08) | Hover backgrounds, subtle fills |

### Semantic Colors
| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `--bg` | #F5F5F0 | #111111 | Page background |
| `--surface` | #FFFFFF | #1C1C1C | Cards, panels, modals |
| `--surface-2` | #F0EDE8 | #252525 | Secondary surfaces, hover |
| `--text-primary` | #1A1A1A | #F0EDE8 | Body text |
| `--text-secondary` | #6B6B6B | #A0A0A0 | Supporting text |
| `--text-muted` | #9E9E9E | #6B6B6B | Labels, captions |
| `--text-metric` | #B8860B | #D4A017 | KPI metric numbers |
| `--border` | #E5E2DC | #2A2A2A | Card/panel borders |
| `--border-subtle` | #EEEBE5 | #222222 | Table row borders |

### Status Colors
| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `--success` / `--success-bg` | #2D7A4F / #E8F5EE | #4CAF7D / rgba | Lunas, Selesai |
| `--danger` / `--danger-bg` | #C0392B / #FDECEA | #E55A4E / rgba | Belum Lunas, SLA overdue |
| `--warning` / `--warning-bg` | #D4A017 / #FDF6E3 | #F5C842 / rgba | Proses, Medium risk |
| `--info` / `--info-bg` | #1A6FA8 / #E3F0FB | #4BA3D9 / rgba | Siap Diambil, Low risk |

### Sidebar (always dark)
| Token | Value | Usage |
|-------|-------|-------|
| `--sidebar-bg` | #0A0A0A | Sidebar background |
| `--sidebar-text` | #A0A0A0 | Inactive nav text |
| `--sidebar-text-active` | #F5C842 | Active nav text |
| `--sidebar-active-bg` | rgba(212,160,23,0.12) | Active nav background |

## Typography

- **Font:** Inter (Google Fonts), with system fallbacks
- **Mono:** JetBrains Mono / Fira Code (for nota numbers)

| Size | Usage |
|------|-------|
| .62-.68rem | Micro labels (card-label, table header, timestamps) |
| .72-.78rem | Small text (badges, controls, subtitles) |
| .82-.88rem | Body text (table cells, nav items, form inputs) |
| .92-.95rem | Panel titles, area card names |
| 1.05-1.2rem | Mobile KPI values |
| 1.35rem | Page titles (header-bar h1) |
| 1.65rem | KPI metric values |

## Spacing & Radius

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-xs` | 4px | Incoming bubble top-left |
| `--radius-sm` | 6px | Buttons, inputs, small elements |
| `--radius-md` | 12px | Cards, panels, chat bubbles |
| `--radius-lg` | 16px | Large cards, modals |
| `--radius-xl` | 24px | (reserved) |
| `--radius-full` | 9999px | Badges, pills, chips, reply input |

## Shadows

| Token | Light | Dark |
|-------|-------|------|
| `--shadow-sm` | 0 1px 4px rgba(0,0,0,0.06) | 0 1px 4px rgba(0,0,0,0.3) |
| `--shadow-card` | 0 2px 12px rgba(0,0,0,0.08) | 0 2px 12px rgba(0,0,0,0.4) |
| `--shadow-hover` | 0 8px 24px rgba(0,0,0,0.12) | 0 8px 24px rgba(0,0,0,0.6) |
| `--shadow-modal` | 0 20px 60px rgba(0,0,0,0.16) | 0 20px 60px rgba(0,0,0,0.8) |

## Component Library

### Sidebar
- Always dark regardless of theme
- SVG stroke icons (18x18, currentColor)
- Active: gold text + gold left border 3px + gold-subtle background
- Logo dot with pulse animation

### Header Bar
- Each page has its own header bar with title + controls
- Theme toggle button (sun/moon icon) on Dashboard page
- 1px bottom border

### KPI Cards
- `.card` class, grid auto-fit min 200px
- Gold metric values (`--text-metric`)
- Hover: translateY(-2px) + shadow-hover + gold-subtle border
- Status sub-text: .up (success), .down (danger), .neutral (muted)

### Data Tables
- Uppercase muted header, 2px bottom border
- Row hover: gold-subtle background
- Clickable rows get pointer cursor

### Badges (Pill Style)
- `.badge` + `.badge-green|red|orange|blue|gold`
- Full-radius pills, semantic bg/text colors
- Works in both light and dark themes

### Charts
- Chart.js 4.4.0
- Theme-aware: colors update when theme toggles
- Gold as primary color, palette rotates through brand-harmonious colors
- Grid/text colors read from CSS variables

### WhatsApp CRM
- 2-column layout (340px list + flex chat)
- Customer avatar: gold gradient background
- Outgoing bubble: gold gradient, white text
- Incoming bubble: surface bg with border
- Date separators: pill-style centered labels
- **Reply input bar** at bottom of chat (new in v3.0)
- Mobile: full-screen chat with back button

### Modals
- Overlay with surface background
- Sticky header with close button
- Detail grid (2-col on desktop, 1-col mobile)

## Theme System

### How It Works
1. `<html>` element gets `data-theme="light"` or `data-theme="dark"`
2. FOUC prevention: inline `<script>` in `<head>` reads localStorage before render
3. CSS variables change via `[data-theme="dark"]` selector
4. `ThemeManager.toggle()` switches, saves to localStorage, refreshes charts
5. Sidebar is always dark (uses fixed `--sidebar-*` tokens)

### Adding New Themed Components
1. Use `var(--token-name)` for all colors, borders, shadows
2. Never hardcode hex colors
3. For new status states, add to both `:root` and `[data-theme="dark"]`
4. For charts, call `chartTheme()` to get current colors

## File Structure
```
/var/www/sijibintaro/dashboard/
├── index.html          (single-file app: CSS + HTML + JS)
├── docs/
│   ├── DESIGN-SYSTEM.md (this file)
│   └── CHANGELOG.md
├── README.md
├── PLAN-v2.2.md
└── PAYMENT-INTEGRATION.md
```

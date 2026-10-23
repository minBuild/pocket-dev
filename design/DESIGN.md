---
name: Pocket Dev
colors:
  surface: '#101419'
  surface-dim: '#101419'
  surface-bright: '#36393f'
  surface-container-lowest: '#0a0e13'
  surface-container-low: '#181c21'
  surface-container: '#1c2025'
  surface-container-high: '#262a30'
  surface-container-highest: '#31353b'
  on-surface: '#e0e2ea'
  on-surface-variant: '#bacac5'
  inverse-surface: '#e0e2ea'
  inverse-on-surface: '#2d3136'
  outline: '#859490'
  outline-variant: '#3c4a46'
  surface-tint: '#3cddc7'
  primary: '#57f1db'
  on-primary: '#003731'
  primary-container: '#2dd4bf'
  on-primary-container: '#00574d'
  inverse-primary: '#006b5f'
  secondary: '#7bd0ff'
  on-secondary: '#00354a'
  secondary-container: '#00a6e0'
  on-secondary-container: '#00374d'
  tertiary: '#e0d3ff'
  on-tertiary: '#381385'
  tertiary-container: '#c6b3ff'
  on-tertiary-container: '#5538a2'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#62fae3'
  primary-fixed-dim: '#3cddc7'
  on-primary-fixed: '#00201c'
  on-primary-fixed-variant: '#005047'
  secondary-fixed: '#c4e7ff'
  secondary-fixed-dim: '#7bd0ff'
  on-secondary-fixed: '#001e2c'
  on-secondary-fixed-variant: '#004c69'
  tertiary-fixed: '#e8ddff'
  tertiary-fixed-dim: '#cebdff'
  on-tertiary-fixed: '#21005e'
  on-tertiary-fixed-variant: '#4f319c'
  background: '#101419'
  on-background: '#e0e2ea'
  surface-variant: '#31353b'
typography:
  headline-lg:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Geist
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Geist
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Geist
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 22px
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  label-code-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
    letterSpacing: -0.01em
  label-code-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0em
  label-code-xs:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.02em
  caption:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 0.75rem
  gutter-mobile: 0.5rem
  margin: 1rem
  margin-mobile: 0.75rem
  space-2xs: 0.125rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style
The design system targets autonomous software engineers, technical founders, and builders orchestrating fleets of Claude AI sub-agents on the move. Its emotional core balances the precision of an IDE terminal with the tactile polish of a native iOS utility. The experience must feel dependable, focused, and low-latency—never game-like or toyish.

The design movement mixes **Minimalist Developer Utility** with **Subtle Neon Precision**:
- High-density layouts organized around clean tabular data and state telemetry.
- Dark-mode surfaces by default, calibrated to minimize eye strain and battery drain on mobile OLED screens. An optional light theme follows the device setting or a per-device 🌗 toggle; the single source of truth for light values is `html[data-theme=light]` in `web/style.css`.
- Monochromatic foundation (slate and graphite) illuminated by purposeful semantic indicators rather than decorative gradients.
- Tactile, hairline-defined components that favor information density, legibility, and immediate terminal status awareness over superfluous ornamentation.

## Colors

### Canvas & Structural Surfaces
- **Canvas Base:** `#0B0F14` (Deep obsidian neutral; root layer for app shell and navigation backdrop).
- **Surface Level 1 (Cards & Rows):** `#131A22` (Subtle blue-gray tint to separate functional cards from canvas).
- **Surface Level 2 (Inputs, Modals, Terminal Panels):** `#18222E` (Interactive and nested container background).
- **Surface Level 3 (Active, Hovered, or Selected Rows):** `#1E2B3A`.

### Borders & Structural Hairlines
- **Subtle Hairline:** `#1E293B` (Primary containment stroke for cards, dividers, and top bars).
- **Interactive Stroke:** `#223042` (Elevated borders on focused inputs, buttons, and popovers).
- **Active Accent Stroke:** `rgba(45, 212, 191, 0.45)` (Subtle highlight for active execution boundaries).

### Accent & Interaction
- **Primary Accent:** `#2DD4BF` (Electric teal for primary execution CTAs, cursor states, global active tabs, and telemetry success pulses).
- **Interactive Hover/Active Teal:** `#14B8A6`.
- **Primary Teal Glow/Muted Wash:** `rgba(45, 212, 191, 0.12)`.

### Typography Colors
- **Text Headings / Emphasized:** `#F8FAFC` (Crisp off-white for primary agent titles, filenames, and view headers).
- **Text Body / Secondary:** `#94A3B8` (Balanced warm slate for chat streams, agent logs, and descriptions).
- **Text Metadata / Muted:** `#64748B` (Muted cool gray for timestamps, token counters, and commit SHAs).

### Multi-Agent Semantic Roles
Specific role tokens allow instant scanning of multi-agent concurrency streams:
- **Planner / PM Agent:** `#A78BFA` (Amethyst purple) | Badge Wash: `rgba(167, 139, 250, 0.14)`.
- **Coder Agent:** `#38BDF8` (Electric sky cyan) | Badge Wash: `rgba(56, 189, 248, 0.14)`.
- **Reviewer Agent:** `#34D399` (Emerald / sage green) | Badge Wash: `rgba(52, 211, 153, 0.14)`.
- **Tester / QA Agent:** `#FB923C` (Coral amber) | Badge Wash: `rgba(251, 146, 60, 0.14)`.
- **System / Orchestrator:** `#2DD4BF` (Teal) | Badge Wash: `rgba(45, 212, 191, 0.14)`.

## Typography

The typographic strategy balances human scanning efficiency with technical precision.

- **Primary UI & Reading:** Set in **Geist**. Used across headers, agent message discourse, dialogs, and task breakdowns. Geist provides a modern, neutral geometric rhythm that avoids visual fatigue during long debugging threads.
- **Technical & Terminal Contexts:** Set in **JetBrains Mono**. Strictly limited to diffs, syntax-highlighted code blocks, telemetry metrics (tokens/sec, context window %), git branches, and model architecture badges (e.g., `claude-3-7-sonnet`).
- **Hierarchy Rules:** 
  - Never set body message prose in monospace; keep conversational agent dialog in `body-md` (Geist).
  - Use `label-code-xs` with uppercase transformation for live status pills (e.g., `RUNNING`, `AWAITING_REVIEW`).
  - Maintain tight, consistent line heights on mobile to preserve screen real estate on 6.1" display viewports.

## Layout & Spacing

The layout is grounded in a strict **8pt rhythm** with a **4pt sub-unit** for tight terminal metadata alignments:
- **Mobile-First Frame:** Engineered specifically for the physical bounds of the iPhone 15 Pro (393 × 852 pt canvas), accommodating dynamic islands, navigation headers, and home indicators.
- **Canvas Margins:** Mobile side margins default to `0.75rem` (12px) to provide maximum utility width for code snippets while avoiding accidental edge touches. Desktop/tablet expands to `1rem` (16px) gutters.
- **Vertical Rhythm:** 
  - Agent message streams use `space-sm` (8px) between contiguous messages from the same agent.
  - Context handoffs between different agents step up to `space-md` (12px) with distinct avatar dividers.
- **Horizontal Density:** Agent status badges and code metrics use `space-xs` (4px) internal vertical padding and `space-sm` (8px) horizontal padding to maximize inline information density.

## Elevation & Depth

Visual hierarchy uses **tonal layering coupled with razor-sharp 1px hairlines** instead of heavy, muddy drop shadows. This preserves developer focus and aligns with native IDE panels.

- **Surface Layering (Z-Index Architecture):**
  - **Level 0 (Base Canvas):** `#0B0F14` flat background.
  - **Level 1 (Card & Thread Surface):** `#131A22` with a `1px solid #1E293B` perimeter stroke.
  - **Level 2 (Active Sub-Panel & Floating Command Palette):** `#18222E` with a `1px solid #223042` stroke and a soft directional glow (`0 8px 24px -4px rgba(0, 0, 0, 0.6)`).
  - **Level 3 (Modal Sheets & Mobile Action Drawers):** `#131A22` resting over an opaque backdrop blur (`backdrop-filter: blur(16px)` with `background: rgba(11, 15, 20, 0.75)`).

- **Glow & Status Accents:**
  - Ambient glows are reserved exclusively for live execution states.
  - Active agents emit a 6px blurred perimeter pulse using their designated role token (e.g., `box-shadow: 0 0 12px rgba(45, 212, 191, 0.2)` for active system prompts).
  - No generalized drop shadows on static buttons or text.

## Shapes

The geometric framework is calculated for a compact, technical mobile application:
- **Cards & Workspace Panels:** Fixed at `12px` border radius (`rounded-md` equivalent in standard 8pt conventions) to establish a refined, pocket-sized container aesthetic without looking overly bubbly.
- **Terminal Snippets & Code Blocks:** `8px` (`rounded-sm`), enforcing structured, squared internal corners for text alignment.
- **Badges, Tags, & Status Indicators:** Full pill (`9999px`) to create an immediate visual contrast against square code containers.
- **Action Buttons & Inputs:** `8px` to maintain a functional tool-like tactile trigger.

## Components

### Buttons
- **Primary Action (Run / Deploy / Dispatch):** 
  - Background: `#2DD4BF`, text: `#0B0F14` (`Geist Medium 14px`), border: `none`.
  - Height: `38px` on mobile for precision tapping. Active state scales slightly (`0.98`) with background `#14B8A6`.
- **Secondary / Agent Command:**
  - Background: `#131A22`, text: `#F8FAFC`, border: `1px solid #1E293B`.
  - Hover/Focus: border `#223042`, background `#18222E`.
- **Ghost / Tool Action:**
  - Background: `transparent`, text: `#94A3B8`, border: `none`.

### Agent Badges & Identity Chips
- Compact horizontal layouts featuring a `6px` circular pulse indicator, agent title, and current task state.
- **Planner Chip:** Border `rgba(167, 139, 250, 0.3)`, Background `rgba(167, 139, 250, 0.1)`, Text `#A78BFA`.
- **Coder Chip:** Border `rgba(56, 189, 248, 0.3)`, Background `rgba(56, 189, 248, 0.1)`, Text `#38BDF8`.
- **Reviewer Chip:** Border `rgba(52, 211, 153, 0.3)`, Background `rgba(52, 211, 153, 0.1)`, Text `#34D399`.
- **Tester Chip:** Border `rgba(251, 146, 60, 0.3)`, Background `rgba(251, 146, 60, 0.1)`, Text `#FB923C`.
- Text is always formatted in `label-code-xs` (JetBrains Mono).

### Agent Collaboration Cards
- Background: `#131A22`, rounded `12px`, outline `1px solid #1E293B`.
- Header houses the agent identity badge, execution duration timestamp (`#64748B`), and quick actions (inspect diff, retry, abort).
- Card interior nests code snippets directly inside an inner panel (`#0B0F14`, rounded `8px`, border `1px solid #1E293B`).

### Code Blocks & Diff Viewers
- Background: `#0B0F14`. Font: `JetBrains Mono 12px`.
- Line numbers: `#64748B` with `padding-right: 12px`.
- Additions: Background `rgba(52, 211, 153, 0.12)`, text `#34D399`.
- Deletions: Background `rgba(244, 63, 94, 0.12)`, text `#F43F5E`.

### Inputs & Prompt Bar
- **Fixed Mobile Prompt Dock:** Pinned to bottom above the iOS home indicator.
- Background: `#131A22` with a `1px solid #223042` border.
- Text: `#F8FAFC`, placeholder: `#64748B`.
- Features an inline agent mention menu (`@coder`, `@reviewer`) that triggers auto-complete pills with agent-specific accent colors.

### Checkboxes & Segmented Controls
- **Segmented Agent Tab Bar:** Pill-style segment wrapper (`#0B0F14`) containing tab triggers. Active trigger features `#18222E` background, `1px solid #1E293B`, and an active bottom marker or label in `#2DD4BF`.
- **Task Toggles:** Square `16px` checkbox with `4px` radius. Unchecked: `1.5px solid #64748B`. Checked: background `#2DD4BF` with dark `#0B0F14` check icon.
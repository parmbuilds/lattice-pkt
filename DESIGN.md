---
name: Lattice
description: Connected knowledge for AI agents.
colors:
  ink: "#121417"
  ink-raised: "#262b2f"
  graphite: "#474e4b"
  muted: "#616a66"
  rule: "#d5dad7"
  rule-strong: "#a3aca8"
  drafting-film: "#eef1ef"
  sheet: "#fcfdfc"
  blueline: "#2b5fae"
  blueline-tint: "#e4ecf8"
  redline: "#d43d2b"
  redline-ink: "#a92d1f"
  redline-tint: "#fbebe8"
  approved: "#1d6a48"
  approved-tint: "#e2f0e8"
  draft: "#845600"
  draft-tint: "#fbf0d8"
  rail-text: "#e6eae8"
  rail-muted: "#98a39e"
  highlight: "#f5e3a1"
typography:
  display:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "IBM Plex Sans Condensed, IBM Plex Sans, ui-sans-serif, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    letterSpacing: "0.08em"
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
rounded:
  stamp: "2px"
  control: "3px"
  sheet: "4px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "{colors.ink-raised}"
  button-secondary:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-danger:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.redline-ink}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  input:
    backgroundColor: "#ffffff"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
    height: "36px"
  stamp-approved:
    backgroundColor: "{colors.approved-tint}"
    textColor: "{colors.approved}"
    typography: "{typography.label}"
    rounded: "{rounded.stamp}"
    height: "22px"
  stamp-draft:
    backgroundColor: "{colors.draft-tint}"
    textColor: "{colors.draft}"
    typography: "{typography.label}"
    rounded: "{rounded.stamp}"
    height: "22px"
  sheet:
    backgroundColor: "{colors.sheet}"
    rounded: "{rounded.sheet}"
---

# Design System: Lattice

## Overview

**Creative North Star: "The Drawing Register"**

Lattice treats every document the way an engineering office treats a drawing: it has a number, a revision letter, a status stamp and a title block that says who issued it and what it is for. Changes are marked in red the way a drafter clouds a revision. The world exists to make governance legible at a glance, so a person always knows which revision serves search and agents and who it reaches.

This is a working tool, used daily and during incidents. The world lends type, palette, density and two signature moves (the title block and the revision cloud); navigation, tables, forms and controls stay the standard web components people already know. Screens are light, dense and ruled: an ink rail, a drafting-film ground, sheets laid on it, hairline rules between rows.

**Key Characteristics:**
- Ink side rail on a drafting-film ground; content sits on near-white sheets with hairline rules.
- State shown by stamps whose wording and border style carry meaning before colour does.
- Condensed caps labels, tabular figures, monospace only for ids, diffs, code and config.
- Red is reserved for changes and refusals.
- Flat: depth comes from rules and tone, never shadows.

## Colors

A restrained drafting palette: neutral greys tinted green, one ink, one blueline, one redline, and two state inks.

### Primary
- **Drawing Ink** (#121417): the rail, the title-block rules, primary buttons and body text.
- **Blueline** (#2b5fae): links, focus rings, selection, caret and form accents. Interaction only.

### Secondary
- **Redline** (#d43d2b): revision clouds, struck deletions, danger buttons and refusals. Text in red uses Redline Ink (#a92d1f) for contrast.

### Tertiary
- **Approved Ink** (#1d6a48) on its tint: approved, serving and allowed stamps.
- **Draft Ink** (#845600) on its tint: draft and pending stamps, always with a dashed border.

### Neutral
- **Drafting Film** (#eef1ef): the page ground.
- **Sheet** (#fcfdfc): tables, forms, document text and every panel laid on the ground.
- **Graphite** (#474e4b) and **Muted** (#616a66): secondary and tertiary text; both pass 4.5:1 on film and sheet.
- **Rule** (#d5dad7) and **Rule Strong** (#a3aca8): row hairlines, panel borders and control borders.
- **Rail Text** (#e6eae8) and **Rail Muted** (#98a39e): text on the ink rail.
- **Highlighter** (#f5e3a1): search match marks only.

### Named Rules
**The Redline Rule.** Red marks a change or a refusal and nothing else: diffs, rejected, denied, withdraw and remove actions, errors.

**The Stamp-Before-Colour Rule.** Every state reads without colour: Approved is solid, Draft is dashed, Withdrawn is struck through, Superseded is a hairline.

## Typography

**Body Font:** IBM Plex Sans (with ui-sans-serif, system-ui)
**Label Font:** IBM Plex Sans Condensed (with IBM Plex Sans)
**Mono Font:** IBM Plex Mono (with ui-monospace)

**Character:** an engineering family: a sober grotesque for reading, its condensed cut in tracked caps for the title-block labels and stamps, and its mono for anything that is data. All faces are self-hosted from the bundle.

### Hierarchy
- **Display** (600, 2.5rem, 1.15): the sign-in statement only.
- **Headline** (600, 1.5rem, 1.2): one page title per screen.
- **Title** (600, 1.0625rem, 1.3): section and panel headings.
- **Body** (400, 0.9375rem, 1.5): everything else; document text capped at 75ch.
- **Label** (600, 0.6875rem, 0.08em tracking, uppercase, condensed): title-block labels, table headers, stamps, nav group labels.
- **Mono** (400, 0.8125rem): ids, token prefixes, audit actions and rules, diffs, client config.

### Named Rules
**The Data-Only Mono Rule.** Monospace marks data a person might copy or compare. Document prose stays in Plex Sans even though it is raw text.

## Layout

A fixed 15rem ink rail on the left from 768px up, holding the workspace switcher, the person and role, and nav grouped as Knowledge, Agents and Governance. Below 768px the rail becomes a top block and the nav wraps into a row. Content sits in a centred column up to 72rem with 40px side padding (20px on mobile). Each page opens with a header (title, one-line description, actions to the right) closed by a hairline rule. Detail and tool pages use a main column plus a 20–22rem side column that stacks under the main column below 1024px. On narrow screens, tables drop secondary columns rather than scroll.

## Elevation & Depth

Flat. There are no shadows. Depth comes from tone (sheet on film, ink rail beside both) and from rules: hairlines between rows, a 1.5px ink rule under table headers and around the title block.

### Named Rules
**The Ruled-Not-Raised Rule.** Separate with a rule or a tone step, never a shadow.

## Shapes

Nearly square. Stamps take 2px corners, controls 3px, sheets 4px. Borders do the structural work: 1px hairlines for panels and rows, 1.5px solid ink for the title block and table headers, 1.5px current-colour borders on stamps.

## Components

### Buttons
- **Shape:** squared (3px), 32px minimum height, 14px medium text.
- **Primary:** ink fill with sheet text; one per area.
- **Secondary:** sheet fill with a Rule Strong border; the border darkens to Graphite on hover.
- **Danger:** sheet fill, redline border and Redline Ink text; tints red on hover. Used for withdraw, remove, reject, revoke, delete.
- **Quiet:** borderless blueline text for row-level actions such as View, Unshare and Cancel invite.
- **Pressed filters:** a filter set of secondary buttons where the active one takes the primary fill and `aria-pressed`.

### Stamps (signature)
- **Style:** condensed caps label in a 22px box with a 1.5px current-colour border; 28px in the large size.
- **States:** Approved and Serving (solid, approved ink), Draft and Edit pending (dashed, draft ink), Withdrawn and Revoked (struck, muted), Superseded and Not approved (hairline, muted), Rejected and Deny (solid, redline ink), Allow (solid, approved ink).

### Title block (signature)
- **Style:** a two-column ruled grid in a 1.5px ink frame; the ink shows through 1px gaps, so every rule is exact whatever cells span. Each cell holds a condensed caps label over a medium value.
- **Fields:** ID, Rev (large condensed letter, "of N"), Author, Date, then full-width rows for what serves search and agents, sharing and the last decision.

### Revision cloud (signature)
- **Style:** a scalloped redline border drawn by a bundled 30×30 SVG used as a `border-image` with round repeat, wrapped around each run of added lines in an approval diff. Removed lines are struck through in red. Unified-diff prefixes stay visible.

### Sheets / Containers
- **Corner Style:** 4px.
- **Background:** Sheet on Drafting Film.
- **Border:** 1px Rule. Panel footers use a 60% film tint for their action rows.
- **Internal Padding:** 20px.

### Inputs / Fields
- **Style:** white fill, 1px Rule Strong border, 3px corners, 36px minimum height; labels sit above in 13px medium Graphite.
- **Focus:** blueline border plus a 2px blueline outline.
- **Textareas:** monospace at 13px for raw document text.

### Navigation
- **Style:** 14px medium rail-text links in grouped lists under condensed caps group labels. The active link inverts to a film cell with ink text and carries `aria-current="page"`. On mobile the groups collapse into one wrapping row.

### Status message
- Rendered empty and hidden until it has something to say (it keeps its polite live region). When text arrives it lands with the one authored motion: a 520ms expo ease-out press from a blueline tint. Disabled under reduced motion.

## Do's and Don'ts

### Do:
- **Do** derive every state label from API facts: REV letters from version order, "Serves search and agents" from the serving version and withdrawal.
- **Do** ship every style as bundled CSS. The CSP is `style-src 'self'`: no `style` attributes, no `:style` bindings, no runtime style tags.
- **Do** keep standard controls (native selects, buttons, tables, details) and let the world show through type, palette, density, stamps, the title block and clouds.
- **Do** keep denial and absence identical: show "Not found or not permitted." and nothing more.

### Don't:
- **Don't** use red for anything but changes and refusals.
- **Don't** use shadows, gradients or glass.
- **Don't** put a label or eyebrow above a heading; condensed caps belong to labels for values, table headers, stamps and nav groups.
- **Don't** show or imply features Lattice does not have (citations, vector search, file upload, SSO).

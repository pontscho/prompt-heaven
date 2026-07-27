---
name: css-guidelines
description: CSS coding guidelines including CSS custom properties, theming, BEM naming conventions, component-based architecture, and modern CSS best practices. Use when writing styles, creating themes, or following CSS/CSS-in-JS patterns.
---

## Skill: p:css-guidelines

**Base directory**: ~/.claude/skills/p/skills/css-guidelines

### 0. Code Implementation Guidelines

Follow these rules when you write CSS:

- Use CSS custom properties (variables) for all design tokens
- Use kebab-case for CSS class names (e.g., `.my-component`, `.is-active`)
- Use BEM-like naming convention: `.component-name`, `.component-name--modifier`, `.component-name__element`
- Use TAB for indentation, DON'T USE SPACE for it
- Use rem units for scalable components, px for borders and hairlines
- Prefer flexbox and grid for layouts over floats
- Use `box-sizing: border-box` globally
- Keep specificity low - avoid deep nesting
- Keep code style of the source file

### 1. Code Documentation Guidelines

- Use CSS comments with `/* === Section Name === */` for major sections
- Use `/* Sub-section */` for minor groupings
- Document complex selectors or calculations
- Comment theme overrides and dark mode styles

Example:
```css
/* === COLORS === */
/* Primary color palette */
--brand-primary-500: #002749;

/* === LAYOUT COMPONENTS === */
/* Flex container for row layouts */
```

### 2. CSS Custom Properties (Variables)

#### Variable Naming Convention
- Prefix variables with project/namespace (e.g., `--renderpark-`)
- Use descriptive, hierarchical names
- Group by category: colors, typography, spacing, etc.

Example structure:
```css
:root {
  /* === COLORS === */
  /* Primary palette */
  --brand-primary-50: #e6f0ff;
  --brand-primary-100: #cce0ff;
  --brand-primary-500: #002749;
  --brand-primary-600: #001f3a;
  
  /* Neutral palette */
  --brand-neutral-100: #f1f5f9;
  --brand-neutral-500: #64748b;
  --brand-neutral-900: #0f172a;
  
  /* Semantic colors */
  --brand-surface-primary: #ffffff;
  --brand-surface-secondary: #f8fafc;
  --brand-text-primary: #000000;
  --brand-text-secondary: #475569;
  --brand-border-primary: #cbd5e1;
  
  /* Status colors */
  --brand-success: #22c55e;
  --brand-error: #ef4444;
  --brand-warning: #f59e0b;
  
  /* === TYPOGRAPHY === */
  --brand-font-family: 'Inter', system-ui, sans-serif;
  --brand-font-size-sm: 0.875rem;
  --brand-font-size-base: 1rem;
  --brand-font-size-lg: 1.125rem;
  
  /* === SPACING === */
  --brand-spacing-1: 0.25rem;
  --brand-spacing-2: 0.5rem;
  --brand-spacing-4: 1rem;
  --brand-spacing-8: 2rem;
  
  /* === BORDERS === */
  --brand-border-width: 1px;
  --brand-border-radius: 0.25rem;
  --brand-radius-md: 0.375rem;
  --brand-radius-lg: 0.5rem;
  
  /* === SHADOWS === */
  --brand-shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --brand-shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);
  
  /* === TRANSITIONS === */
  --brand-transition-fast: 150ms ease;
  --brand-transition-normal: 300ms ease;
  
  /* === Z-INDEX === */
  --brand-z-dropdown: 1000;
  --brand-z-modal: 1050;
  --brand-z-tooltip: 1100;
}
```

### 3. Theming System

#### Data Attribute Based Themes
Use data attributes for theme switching (not class-based):

```css
/* === DEFAULT / LIGHT THEME === */
[data-theme="light"],
:root {
  --brand-surface-primary: #ffffff;
  --brand-surface-secondary: #f8fafc;
  --brand-text-primary: #000000;
  --brand-border-primary: #cbd5e1;
}

/* === DARK THEME === */
[data-theme="dark"] {
  --brand-surface-primary: #0f172a;
  --brand-surface-secondary: #1e293b;
  --brand-text-primary: #f8fafc;
  --brand-border-primary: #334155;
}

/* Theme transitions */
[data-theme],
[data-theme] * {
  transition: background-color 0.3s ease, color 0.3s ease, border-color 0.3s ease;
}
```

#### Component-Specific Dark Mode Overrides
```css
/* Dark theme overrides for specific elements */
[data-theme="dark"] .brand-accordion-summary {
  color: var(--brand-text-primary);
}

[data-theme="dark"] .brand-menu-item {
  color: var(--brand-text-primary);
}
```

### 4. Component Architecture

#### Base Styles
Always include box-sizing reset and base body styles:

```css
/* === BASE STYLES === */
*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: var(--brand-font-family);
  font-size: var(--brand-font-size-base);
  line-height: var(--brand-line-height-normal);
  color: var(--brand-text-primary);
  background-color: var(--brand-surface-primary);
}
```

#### Component Structure
```css
/* === COMPONENT NAME === */
.component-name {
  /* Base styles */
}

.component-name:hover {
  /* Hover state */
}

.component-name:focus {
  /* Focus state */
}

.component-name:disabled,
.component-name.disabled {
  /* Disabled state */
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

/* Modifiers */
.component-name--modifier {
  /* Modified variant */
}

/* Elements (if not using full BEM) */
.component-name-element {
  /* Child element */
}
```

### 5. Layout Components

#### Flexbox Patterns
```css
/* === ROW === */
.brand-row {
  display: flex;
  flex-direction: row;
  align-items: center;
}

/* === COLUMN === */
.brand-column {
  display: flex;
  flex-direction: column;
}

/* === SPACER === */
.brand-spacer {
  display: inline-block;
}

.brand-spacer.horizontal {
  width: var(--brand-spacing-4);
  height: 1px;
}

.brand-spacer.vertical {
  height: var(--brand-spacing-4);
  width: 1px;
}
```

### 6. Form Components

#### Input Patterns
```css
.brand-textfield {
  height: var(--brand-textfield-height, 2.5rem);
  padding: var(--brand-spacing-2) var(--brand-spacing-3);
  font-size: var(--brand-font-size-sm);
  font-family: var(--brand-font-family);
  border: var(--brand-border-width) solid var(--brand-border-primary);
  border-radius: var(--brand-border-radius);
  background-color: var(--brand-surface-primary);
  color: var(--brand-text-primary);
  transition: border-color var(--brand-transition-fast), 
              box-shadow var(--brand-transition-fast);
  width: 100%;
  box-sizing: border-box;
}

.brand-textfield:focus {
  outline: none;
  border-color: var(--brand-primary-500);
  box-shadow: 0 0 0 2px var(--brand-primary-100);
}

.brand-textfield:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.brand-textfield.is-invalid,
.brand-textfield-invalid {
  border-color: var(--brand-error);
}

.brand-textfield.is-invalid:focus {
  border-color: var(--brand-error);
  box-shadow: 0 0 0 2px var(--brand-error-bg);
}
```

#### Button Patterns
```css
.brand-button {
  height: var(--brand-button-height, 2.25rem);
  padding: 0 var(--brand-spacing-4);
  font-size: var(--brand-font-size-sm);
  font-weight: var(--brand-font-weight-medium);
  font-family: var(--brand-font-family);
  border-radius: var(--brand-border-radius);
  border: var(--brand-border-width) solid transparent;
  cursor: pointer;
  transition: all var(--brand-transition-fast);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--brand-spacing-2);
}

.brand-button:hover {
  opacity: 0.9;
}

.brand-button:disabled,
.brand-button.disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

/* Button variants */
.brand-button.btn-primary {
  background-color: var(--brand-primary-500);
  color: var(--brand-text-inverse);
  border-color: var(--brand-primary-500);
}

.brand-button.btn-secondary {
  background-color: var(--brand-surface-secondary);
  color: var(--brand-text-primary);
  border-color: var(--brand-border-primary);
}

.brand-button.btn-error {
  background-color: var(--brand-error);
  color: var(--brand-text-inverse);
  border-color: var(--brand-error);
}
```

### 7. Navigation Components

#### Menu Patterns
```css
/* === MENUBAR === */
.brand-menubar {
  display: flex;
  list-style: none;
  margin: 0;
  padding: 0;
  background-color: var(--brand-surface-secondary);
  border-bottom: var(--brand-border-width) solid var(--brand-border-primary);
  min-height: var(--brand-menubar-height, 2.5rem);
  align-items: center;
}

.brand-menu-item {
  position: relative;
  padding: var(--brand-spacing-2) var(--brand-spacing-3);
  cursor: pointer;
  font-family: var(--brand-font-family);
  font-size: var(--brand-font-size-sm);
  color: var(--brand-text-primary);
}

.brand-menu-item:hover {
  background-color: var(--brand-neutral-200);
}

/* Submenu */
.brand-submenu {
  position: absolute;
  top: 100%;
  left: 0;
  min-width: 10rem;
  background-color: var(--brand-surface-primary);
  border: var(--brand-border-width) solid var(--brand-border-primary);
  border-radius: var(--brand-border-radius);
  box-shadow: var(--brand-shadow-lg);
  list-style: none;
  margin: 0;
  padding: var(--brand-spacing-1);
  z-index: var(--brand-z-dropdown);
}
```

### 8. Typography

#### Header Patterns
```css
.brand-header {
  margin: 0;
  padding: 0;
  font-family: var(--brand-font-family);
  color: var(--brand-text-primary);
}

.brand-header.size-1 {
  font-size: var(--brand-font-size-3xl);
  font-weight: var(--brand-font-weight-bold);
}

.brand-header.size-2 {
  font-size: var(--brand-font-size-2xl);
  font-weight: var(--brand-font-weight-bold);
}

.brand-header.size-3 {
  font-size: var(--brand-font-size-xl);
  font-weight: var(--brand-font-weight-bold);
}

.brand-header.bold {
  font-weight: var(--brand-font-weight-bold);
}

.brand-header.italic {
  font-style: italic;
}

.brand-header.light {
  font-weight: var(--brand-font-weight-normal);
  color: var(--brand-text-secondary);
}
```

### 9. Collapsible Components

#### Accordion Pattern
```css
.brand-accordion {
  border: var(--brand-border-width) solid var(--brand-border-primary);
  border-radius: var(--brand-border-radius);
  box-shadow: var(--brand-shadow-sm);
  overflow: hidden;
  margin-bottom: var(--brand-spacing-4);
  background-color: var(--brand-surface-primary);
}

.brand-accordion-summary {
  width: 100%;
  padding: var(--brand-spacing-4);
  background-color: var(--brand-surface-secondary);
  border: none;
  text-align: left;
  cursor: pointer;
  font-size: var(--brand-font-size-base);
  font-weight: var(--brand-font-weight-medium);
  font-family: var(--brand-font-family);
  color: var(--brand-text-primary);
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background-color var(--brand-transition-fast);
}

.brand-accordion-summary:hover:not(:disabled) {
  background-color: var(--brand-neutral-200);
}

.brand-accordion-icon {
  font-size: var(--brand-font-size-sm);
  transition: transform var(--brand-transition-normal);
}

.brand-accordion-content {
  padding: var(--brand-spacing-4);
  background-color: var(--brand-surface-primary);
  border-top: 1px solid var(--brand-border-primary);
}
```

#### Tabs Pattern
```css
.brand-tab {
  border-radius: var(--brand-border-radius);
  box-shadow: var(--brand-shadow-md);
  overflow: hidden;
  background-color: var(--brand-surface-primary);
}

.brand-tab-headers {
  display: inline-flex;
  background-color: var(--brand-surface-secondary);
  border-bottom: var(--brand-border-width) solid var(--brand-border-primary);
}

.brand-tab-header {
  padding: var(--brand-spacing-3) var(--brand-spacing-4);
  font-size: var(--brand-font-size-sm);
  font-weight: var(--brand-font-weight-medium);
  font-family: var(--brand-font-family);
  color: var(--brand-text-secondary);
  background: transparent;
  border: none;
  border-bottom: var(--brand-border-width) solid transparent;
  cursor: pointer;
  transition: all var(--brand-transition-fast);
}

.brand-tab-header:hover:not(.disabled) {
  color: var(--brand-text-primary);
  background-color: var(--brand-neutral-200);
}

.brand-tab-header.active {
  color: var(--brand-primary-600);
  background-color: var(--brand-surface-primary);
  border-bottom-color: var(--brand-primary-500);
}

.brand-tab-header.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.brand-tab-content {
  padding: var(--brand-spacing-4);
}
```

### 10. Side Navigation

```css
.brand-side-menu {
  position: fixed;
  top: 0;
  height: 100vh;
  width: var(--brand-sidemenu-width, 16rem);
  background-color: var(--brand-surface-secondary);
  border-right: 1px solid var(--brand-border-primary);
  transition: width var(--brand-transition-normal);
  z-index: var(--brand-z-dropdown);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.brand-side-menu.collapsed {
  width: var(--brand-sidemenu-collapsed-width, 3.75rem);
}

.brand-side-menu.left {
  left: 0;
}

.brand-side-menu.right {
  right: 0;
}

.brand-side-menu-toggle {
  background: none;
  border: none;
  font-size: var(--brand-font-size-lg);
  cursor: pointer;
  padding: var(--brand-spacing-2);
  border-radius: var(--brand-border-radius);
  color: var(--brand-text-secondary);
}

.brand-side-menu-toggle:hover {
  background-color: var(--brand-neutral-300);
  color: var(--brand-text-primary);
}
```

### 11. Utility Classes

```css
/* === UTILITY CLASSES === */
.brand-hidden {
  display: none !important;
}

.brand-disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

.brand-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
```

### 12. File Organization Template

#### global.css
```css
/**
 * @file global.css
 * @brief Main entry point for all styles
 */

@import './variables.css';
@import './theme.css';
@import './components.css';
```

#### variables.css
```css
/**
 * @file variables.css
 * @brief All CSS custom properties (design tokens)
 */

:root {
  /* === COLORS === */
  /* Primary palette */
  --brand-primary-50: #e6f0ff;
  --brand-primary-500: #002749;
  --brand-primary-600: #001f3a;
  
  /* Neutral palette */
  --brand-neutral-100: #f1f5f9;
  --brand-neutral-500: #64748b;
  --brand-neutral-900: #0f172a;
  
  /* Semantic colors */
  --brand-surface-primary: #ffffff;
  --brand-text-primary: #000000;
  --brand-border-primary: #cbd5e1;
  
  /* === TYPOGRAPHY === */
  --brand-font-family: 'Inter', system-ui, sans-serif;
  --brand-font-size-base: 1rem;
  
  /* === SPACING === */
  --brand-spacing-4: 1rem;
  
  /* === SHADOWS === */
  --brand-shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  
  /* === Z-INDEX === */
  --brand-z-dropdown: 1000;
}
```

#### theme.css
```css
/**
 * @file theme.css
 * @brief Light and dark theme definitions
 */

/* === DEFAULT / LIGHT THEME === */
[data-theme="light"],
:root {
  --brand-surface-primary: #ffffff;
  --brand-surface-secondary: #f8fafc;
  --brand-text-primary: #000000;
}

/* === DARK THEME === */
[data-theme="dark"] {
  --brand-surface-primary: #0f172a;
  --brand-surface-secondary: #1e293b;
  --brand-text-primary: #f8fafc;
}

/* Theme transition */
[data-theme],
[data-theme] * {
  transition: background-color 0.3s ease, color 0.3s ease;
}
```

#### components.css
```css
/**
 * @file components.css
 * @brief All component styles
 */

/* === BASE STYLES === */
*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: var(--brand-font-family);
  font-size: var(--brand-font-size-base);
  color: var(--brand-text-primary);
  background-color: var(--brand-surface-primary);
}

/* === LAYOUT === */
.brand-row { /* ... */ }
.brand-column { /* ... */ }

/* === INPUTS === */
.brand-textfield { /* ... */ }
.brand-button { /* ... */ }
.brand-checkbox { /* ... */ }

/* === NAVIGATION === */
.brand-menubar { /* ... */ }
.brand-side-menu { /* ... */ }

/* === CONTENT === */
.brand-header { /* ... */ }
.brand-accordion { /* ... */ }
.brand-tab { /* ... */ }

/* === UTILITIES === */
.brand-hidden { display: none !important; }
```

### 13. Performance Best Practices

- Use CSS containment for complex components: `contain: layout style paint`
- Prefer `transform` and `opacity` for animations (GPU accelerated)
- Avoid `@import` in production (use build tool concatenation)
- Minimize repaints and reflows
- Use `will-change` sparingly and remove after animation
- Avoid universal selectors (*) in key selectors
- Use CSS Grid for 2D layouts, Flexbox for 1D layouts

### 14. Accessibility

- Ensure color contrast meets WCAG 2.1 AA standards (4.5:1 for normal text)
- Support `prefers-reduced-motion` media query
- Use semantic HTML elements
- Provide focus indicators
- Test with keyboard navigation
- Don't rely solely on color for information

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

## Summary

This skill provides CSS coding best practices:
- **CSS Variables**: Project-prefixed custom properties for all design tokens
- **Theming**: Data-attribute based light/dark themes with smooth transitions
- **Naming**: BEM-like convention with kebab-case
- **Architecture**: Component-based organization with base/modifier patterns
- **Layout**: Flexbox and Grid for modern responsive designs
- **File Structure**: Variables, theme, and components separation
- **Performance**: GPU-accelerated properties and containment
- **Accessibility**: Contrast, motion preferences, and semantic structure

For project-specific components and design tokens, refer to project CSS files.
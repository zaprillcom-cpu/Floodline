/**
 * Design tokens for the citizen app.
 * Raycast-like dark surfaces (near-black, hairline strokes, blur glass)
 * and Apple iOS dark colors (system blue, red, green, grouped radii).
 * globals.css only references the variables emitted by `themeStyle()`.
 */

export const theme = {
  color: {
    bg: "#000000",
    elevated: "#1C1C1E",
    grouped: "#2C2C2E",
    glass: "rgba(18, 18, 20, 0.72)",
    glassStrong: "rgba(12, 12, 14, 0.82)",
    stroke: "rgba(255, 255, 255, 0.10)",
    strokeStrong: "rgba(255, 255, 255, 0.18)",
    label: "#FFFFFF",
    secondary: "rgba(235, 235, 245, 0.62)",
    tertiary: "rgba(235, 235, 245, 0.36)",
    separator: "rgba(84, 84, 88, 0.65)",
    fill: "rgba(118, 118, 128, 0.24)",
    blue: "#0A84FF",
    green: "#30D158",
    orange: "#FF9F0A",
    red: "#FF453A",
    purple: "#BF5AF2",
    cyan: "#64D2FF",
    route: "#7DD3FC",
    routeCasing: "#083044",
    closed: "#FF453A",
    flood: "#16324A",
    building: "#1A1C22",
    buildingFlood: "#143044",
  },
  radius: { sm: 10, md: 14, lg: 22, xl: 28, pill: 999 },
  blur: "saturate(180%) blur(40px)",
  shadow: "0 24px 80px rgba(0, 0, 0, 0.48)",
  font: {
    sans: 'var(--font-inter), -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif',
    display: 'var(--font-inter), -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif',
    mono: 'var(--font-mono), "SF Mono", ui-monospace, Menlo, monospace',
  },
} as const;

export function themeStyle(): string {
  const c = theme.color;
  const r = theme.radius;
  return `:root{
    --bg:${c.bg};
    --elevated:${c.elevated};
    --grouped:${c.grouped};
    --glass:${c.glass};
    --glass-strong:${c.glassStrong};
    --stroke:${c.stroke};
    --stroke-strong:${c.strokeStrong};
    --label:${c.label};
    --secondary:${c.secondary};
    --tertiary:${c.tertiary};
    --separator:${c.separator};
    --fill:${c.fill};
    --blue:${c.blue};
    --green:${c.green};
    --orange:${c.orange};
    --red:${c.red};
    --purple:${c.purple};
    --cyan:${c.cyan};
    --route:${c.route};
    --closed:${c.closed};
    --radius-sm:${r.sm}px;
    --radius-md:${r.md}px;
    --radius-lg:${r.lg}px;
    --radius-xl:${r.xl}px;
    --blur:${theme.blur};
    --shadow:${theme.shadow};
    --font-sans:${theme.font.sans};
    --font-display:${theme.font.display};
    --font-mono:${theme.font.mono};
  }`;
}

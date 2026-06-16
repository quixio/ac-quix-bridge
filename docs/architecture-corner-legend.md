# Architecture: Corner Legend Table

## What it does

Adds a compact corner legend table to the right of the Plotly track map inside the TRACK MAP top-bar panel. Each row shows a colored severity dot, the corner designation (T1, T2, ...), and the corner name. The legend scrolls independently when the corner list exceeds the panel height.

## Key decisions

- **Side-by-side layout via wrapper div.** A new `.map-body-row` flex container wraps `#track-map` and `#corner-legend` horizontally, while the parent `.topbar-body` remains a column flex so the zoom slider stays below both. This avoids changing the existing `#track-map` flex behavior.
- **Fixed legend width (160px).** The legend has `flex-shrink: 0` so it never compresses; the track map takes all remaining space. 160px fits "T12" + a ~20-character corner name comfortably at 10px font.
- **Populated inside `renderTrackMap()`.** The legend reads from `trackData.corners` and `trackConfig.colors`, both guaranteed to be loaded when `renderTrackMap()` runs. Placing the population logic right after the Plotly render ensures the legend always reflects the current track data.
- **No separate data fetch.** Corner data is already in the `/api/track` response -- the legend reuses it.

## Data flow

```
fetchTrack()
  -> trackData.corners  (array of {label, name, severity, ...})
  -> trackConfig.colors  (map: severity -> hex color)
  -> renderTrackMap()
      -> Plotly.newPlot(...)   (track map with corner badges)
      -> #corner-legend.innerHTML = corners.map(...)   (legend rows)
```

## File inventory

| File | Action | What changed |
|------|--------|--------------|
| `telemetry-comparison/static/index.html` | Modified | CSS: `.map-body-row`, `#corner-legend`, `.legend-row/dot/label/name` styles. HTML: wrapped `#track-map` + `#corner-legend` in `.map-body-row`. JS: 10 lines at end of `renderTrackMap()` to populate legend. |
| `docs/architecture-corner-legend.md` | Created | This file. |

## Integration with neighboring features

- **Collapse behavior:** `#corner-legend` is inside `.topbar-body`, which is hidden when `.topbar-panel.collapsed` is active. No special handling needed.
- **Zoom slider:** The zoom row is a sibling of `.map-body-row` inside the column-flex `.topbar-body`, so it remains full-width below both map and legend.
- **Track map rendering:** The Plotly div (`#track-map`) keeps its `flex: 1; min-height: 0` rule. The `.map-body-row` wrapper takes `flex: 1` from the column parent, so the map still fills available vertical space.

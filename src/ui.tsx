/**
 * Pieces both cards share: the scoped stylesheet, the card shell (header, footer, chips), the loading
 * skeleton, two small hooks and the cached layer loader.
 *
 * Colours come from the host theme (Hermes Desktop's `--ui-*` tokens, with the friendlier
 * `--card` / `--foreground` names and CSS system colours as fallbacks), so the card follows light,
 * dark and every skin. The only fixed colours are the change palette (it must match the PNG panel)
 * and the on-image chrome (labels and the slider handle sit on imagery, not on the theme).
 */

import type { PluginRestOptions } from '@hermes/plugin-sdk'
import { type ReactNode, useEffect, useState } from 'react'

import {
  type Chip,
  confidenceLabel,
  dateRange,
  eyebrow,
  type Layers,
  type OverheadResult,
  readLayers,
  statChips
} from './lib.ts'

export type Rest = <T>(path: string, opts?: PluginRestOptions) => Promise<T>
export type OpenExternal = ((url: string) => void) | null

const CSS = `
.ovh-card{
  --ovh-surface:var(--ui-widget-surface-background,var(--card,Canvas));
  --ovh-fg:var(--ui-text-primary,var(--foreground,CanvasText));
  --ovh-fg2:var(--ui-text-secondary,var(--muted-foreground,CanvasText));
  --ovh-fg3:var(--ui-text-tertiary,var(--muted-foreground,GrayText));
  --ovh-line:var(--ui-stroke-secondary,var(--border,color-mix(in srgb,CanvasText 14%,transparent)));
  --ovh-hairline:var(--ui-stroke-tertiary,var(--border,color-mix(in srgb,CanvasText 9%,transparent)));
  --ovh-accent:var(--ui-accent,var(--accent,Highlight));
  --ovh-fill:var(--ui-bg-tertiary,color-mix(in srgb,CanvasText 6%,transparent));
  --ovh-fill2:var(--ui-bg-primary,color-mix(in srgb,CanvasText 11%,transparent));
  --ovh-hover:var(--ui-control-hover-background,color-mix(in srgb,CanvasText 7%,transparent));
  --ovh-green:var(--ui-green,#1f8a65);
  --ovh-yellow:var(--ui-yellow,#c08532);
  --ovh-stage:color-mix(in srgb,var(--ovh-fg) 7%,transparent);
  box-sizing:border-box;display:block;width:100%;max-width:720px;margin:2px 0;
  container-type:inline-size;
  background:var(--ovh-surface);color:var(--ovh-fg);
  border:1px solid var(--ovh-hairline);border-radius:12px;
  font-family:inherit;font-size:13px;line-height:1.45;text-align:start;
  white-space:normal;overflow-wrap:normal;word-break:normal;
}
.ovh-card *,.ovh-card *::before,.ovh-card *::after{box-sizing:border-box}
.ovh-head{padding:12px 14px 10px}
.ovh-eyebrow{margin:0 0 2px;font-size:10.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ovh-fg3)}
.ovh-titlerow{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:2px 14px}
.ovh-title{margin:0;font-size:15px;font-weight:600;line-height:1.3;letter-spacing:-.005em;color:var(--ovh-fg)}
.ovh-dates{font-size:12.5px;color:var(--ovh-fg2);font-variant-numeric:tabular-nums;white-space:nowrap}
.ovh-body{display:grid;gap:14px;padding:0 14px 14px}
@container (min-width:600px){.ovh-body{grid-template-columns:minmax(0,3fr) minmax(0,2fr);align-items:start}}
.ovh-media{min-width:0}
.ovh-aside{display:flex;flex-direction:column;gap:12px;min-width:0}
.ovh-headline{margin:0;font-size:14px;font-weight:500;line-height:1.45;color:var(--ovh-fg);text-wrap:pretty}
.ovh-chips{display:flex;flex-wrap:wrap;gap:6px}
.ovh-chip{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 9px;border-radius:999px;background:var(--ovh-fill);color:var(--ovh-fg);font-size:12px;font-weight:500;font-variant-numeric:tabular-nums;white-space:nowrap}
.ovh-chip[data-tone=neutral]{color:var(--ovh-fg2)}
.ovh-dot{flex:none;width:7px;height:7px;border-radius:50%}

.ovh-stage{position:relative;width:100%;overflow:hidden;border-radius:8px;background:var(--ovh-stage);isolation:isolate;user-select:none;-webkit-user-select:none}
.ovh-swipe{cursor:ew-resize;touch-action:pan-y}
.ovh-card img.ovh-layer{position:absolute;inset:0;display:block;width:100%;height:100%;max-width:none;margin:0;padding:0;border:0;border-radius:0;object-fit:fill;pointer-events:none;-webkit-user-drag:none}
.ovh-overlay{transition:opacity .2s ease}
.ovh-side{position:absolute;inset:0;z-index:2;pointer-events:none}
.ovh-label{position:absolute;top:8px;z-index:2;padding:3px 8px;border-radius:999px;background:rgba(11,13,16,.62);color:#fff;font-size:11px;font-weight:500;line-height:1.3;white-space:nowrap;font-variant-numeric:tabular-nums;pointer-events:none;transition:opacity .15s;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.ovh-label b{font-weight:650}
.ovh-label-before{left:8px}
.ovh-label-after{right:8px}
.ovh-divider{position:absolute;top:0;bottom:0;z-index:3;width:2px;margin-left:-1px;background:#fff;box-shadow:0 0 0 .5px rgba(0,0,0,.25),0 0 8px rgba(0,0,0,.35);pointer-events:none}
.ovh-handle{position:absolute;top:50%;z-index:4;width:44px;height:44px;transform:translate(-50%,-50%);display:flex;align-items:center;justify-content:center;cursor:ew-resize;touch-action:none;outline:none}
.ovh-knob{position:relative;display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:#fff;color:#1b1d21;box-shadow:0 1px 4px rgba(0,0,0,.35),0 0 0 1px rgba(0,0,0,.06);transition:transform .12s ease,box-shadow .12s ease}
.ovh-handle:hover .ovh-knob,.ovh-stage[data-dragging] .ovh-knob{transform:scale(1.08)}
.ovh-handle:focus-visible .ovh-knob{box-shadow:0 0 0 3px var(--ovh-accent),0 1px 4px rgba(0,0,0,.35)}

.ovh-change{display:flex;flex-direction:column;gap:8px;padding-top:12px;border-top:1px solid var(--ovh-hairline)}
.ovh-card .ovh-toggle{all:unset;display:inline-flex;align-items:center;gap:8px;align-self:flex-start;border-radius:6px;font-size:12.5px;font-weight:500;color:var(--ovh-fg);cursor:pointer}
.ovh-track{position:relative;flex:none;width:28px;height:16px;border-radius:999px;background:var(--ovh-fill2);box-shadow:inset 0 0 0 1px var(--ovh-line);transition:background .15s ease}
.ovh-track::after{content:"";position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.3);transition:transform .15s ease}
.ovh-toggle[aria-checked=true] .ovh-track{background:var(--ovh-accent);box-shadow:none}
.ovh-toggle[aria-checked=true] .ovh-track::after{transform:translateX(12px)}
.ovh-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11.5px;color:var(--ovh-fg2);transition:opacity .15s ease}
.ovh-legend[data-off]{opacity:.55}
.ovh-legend span{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.ovh-swatch{flex:none;width:10px;height:10px;border-radius:3px}

.ovh-foot{display:flex;flex-direction:column;gap:6px;padding:10px 14px 12px;border-top:1px solid var(--ovh-hairline)}
.ovh-meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;font-size:11.5px;color:var(--ovh-fg3)}
.ovh-meta-text{display:inline-flex;flex-wrap:wrap;gap:0 6px;font-variant-numeric:tabular-nums}
.ovh-meta-text span+span::before{content:"·";margin-right:6px}
.ovh-spacer{flex:1 1 0;min-width:0}
.ovh-badge{display:inline-flex;align-items:center;gap:5px;height:20px;padding:0 8px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap;background:var(--ovh-fill);color:var(--ovh-fg2)}
.ovh-badge[data-level=high]{background:color-mix(in srgb,var(--ovh-green) 14%,transparent);color:color-mix(in srgb,var(--ovh-green) 78%,var(--ovh-fg))}
.ovh-badge[data-level=low]{background:color-mix(in srgb,var(--ovh-yellow) 18%,transparent);color:color-mix(in srgb,var(--ovh-yellow) 78%,var(--ovh-fg))}
.ovh-badge i{width:6px;height:6px;border-radius:50%;background:currentColor}
.ovh-card .ovh-btn{all:unset;display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:8px;box-shadow:inset 0 0 0 1px var(--ovh-line);font-size:12px;font-weight:500;color:var(--ovh-fg);white-space:nowrap;cursor:pointer;transition:background .12s ease}
.ovh-card .ovh-btn:hover{background:var(--ovh-hover)}
.ovh-notes{display:flex;flex-direction:column;gap:2px;font-size:11.5px;line-height:1.4;color:var(--ovh-fg3)}

.ovh-frames .ovh-layer{opacity:0;transition:opacity .18s linear}
.ovh-frames .ovh-layer[data-on]{opacity:1}
.ovh-flag{position:absolute;top:8px;right:8px;z-index:2;display:inline-flex;align-items:center;gap:5px;padding:3px 8px;border-radius:999px;background:rgba(11,13,16,.62);color:#ffd37a;font-size:11px;font-weight:600;pointer-events:none;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.ovh-player{display:flex;align-items:center;gap:10px;margin-top:10px}
.ovh-card .ovh-play{all:unset;flex:none;display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;background:var(--ovh-fg);color:var(--ovh-surface);cursor:pointer;transition:transform .12s ease}
.ovh-card .ovh-play:active{transform:scale(.94)}
.ovh-card .ovh-range{flex:1 1 auto;min-width:0;height:16px;margin:0;accent-color:var(--ovh-accent);cursor:pointer;background:transparent}
.ovh-frame-date{flex:none;font-size:12px;color:var(--ovh-fg2);font-variant-numeric:tabular-nums;white-space:nowrap}
.ovh-readout{display:flex;flex-direction:column;gap:1px}
.ovh-readout-label{font-size:11.5px;color:var(--ovh-fg3)}
.ovh-readout-value{font-size:24px;font-weight:600;line-height:1.2;letter-spacing:-.01em;font-variant-numeric:tabular-nums;color:var(--ovh-fg)}
.ovh-readout-sub{font-size:12px;color:var(--ovh-fg2);font-variant-numeric:tabular-nums}
.ovh-warn{display:flex;align-items:flex-start;gap:6px;font-size:12px;line-height:1.4;color:color-mix(in srgb,var(--ovh-yellow) 80%,var(--ovh-fg))}
.ovh-warn svg{flex:none;margin-top:2px}
.ovh-chart{padding:0 14px 12px}
.ovh-chart>svg{display:block;width:100%;overflow:visible}
.ovh-chart-key svg{flex:none;display:block}
.ovh-chart text{font-size:10.5px;fill:var(--ovh-fg3);font-variant-numeric:tabular-nums}
.ovh-chart-key{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:4px;font-size:11px;color:var(--ovh-fg3)}
.ovh-chart-key span{display:inline-flex;align-items:center;gap:6px}
.ovh-pt{cursor:pointer}

.ovh-card :is(.ovh-btn,.ovh-toggle,.ovh-play,.ovh-range):focus-visible{outline:2px solid var(--ovh-accent);outline-offset:2px}
.ovh-skel{border-radius:8px;background:linear-gradient(90deg,var(--ovh-fill) 0%,var(--ovh-fill2) 50%,var(--ovh-fill) 100%);background-size:200% 100%;animation:ovh-shimmer 1.4s ease-in-out infinite}
.ovh-skel-line{height:12px;border-radius:6px}
@keyframes ovh-shimmer{from{background-position:100% 0}to{background-position:-100% 0}}
@media (prefers-reduced-motion:reduce){
  .ovh-skel{animation:none}
  .ovh-frames .ovh-layer,.ovh-overlay,.ovh-knob,.ovh-track,.ovh-track::after{transition:none}
}
`

// ---------------------------------------------------------------------------------------- icons

export function PlayIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16">
      <path d="M5 3.2v9.6c0 .5.5.8.9.5l7.4-4.8a.6.6 0 0 0 0-1L5.9 2.7C5.5 2.4 5 2.7 5 3.2z" fill="currentColor" />
    </svg>
  )
}

export function PauseIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16">
      <rect x="4" y="3" width="3" height="10" rx="1" fill="currentColor" />
      <rect x="9" y="3" width="3" height="10" rx="1" fill="currentColor" />
    </svg>
  )
}

function ExternalIcon() {
  return (
    <svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3" />
    </svg>
  )
}

export function ChevronsIcon() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5" />
    </svg>
  )
}

export function WarnIcon() {
  return (
    <svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2.2 14.5 13.5h-13L8 2.2zM8 6.5v3.2M8 11.6v.1" />
    </svg>
  )
}

// ---------------------------------------------------------------------------------------- shell

const TONE_COLOR = { loss: '#E5484D', gain: '#30A46C' } as const

export function Chips({ chips, focus }: { chips: Chip[]; focus?: string }) {
  if (!chips.length) return null
  return (
    <div className="ovh-chips">
      {chips.map(chip => (
        <span className="ovh-chip" data-tone={chip.tone} key={chip.label}>
          {chip.tone !== 'neutral' && (
            <i className="ovh-dot" style={{
              background: chip.tone === 'gain' && focus === 'water' ? '#0090FF' : TONE_COLOR[chip.tone]
            }} />
          )}
          {chip.label}
        </span>
      ))}
    </div>
  )
}

/** The card root: scoped styles, header, then the body the card draws. `not-prose` opts the card out
 *  of the transcript's Tailwind Typography styles. */
export function Shell({ r, label, children, busy }: {
  r: OverheadResult
  label: string
  children: ReactNode
  busy?: boolean
}) {
  return (
    <section className="ovh-card not-prose" aria-label={label} aria-busy={busy || undefined}>
      <style>{CSS}</style>
      <header className="ovh-head">
        <div className="ovh-eyebrow">{eyebrow(r)}</div>
        <div className="ovh-titlerow">
          <div className="ovh-title" role="heading" aria-level={3} title={r.place.full_name || undefined}>
            {r.place.name}
          </div>
          <span className="ovh-dates">{dateRange(r)}</span>
        </div>
      </header>
      {children}
    </section>
  )
}

export function Footer({ r, scenes, mapUrl, openExternal }: {
  r: OverheadResult
  scenes: string[]
  mapUrl: string
  openExternal: OpenExternal
}) {
  const confidence = confidenceLabel(r.confidence)
  const level = r.confidence === 'high' || r.confidence === 'low' ? r.confidence : undefined
  return (
    <footer className="ovh-foot">
      <div className="ovh-meta">
        <span className="ovh-meta-text">
          <span>Sentinel-2 L2A · Earth Search</span>
          {scenes.filter(Boolean).map(s => <span key={s}>{s}</span>)}
        </span>
        {confidence && (
          <span className="ovh-badge" data-level={level}
            title={level === 'low' ? 'Less than 70% of the area had a clear view in both scenes.' : undefined}>
            <i />{confidence}
          </span>
        )}
        <span className="ovh-spacer" />
        {mapUrl && openExternal && (
          <button type="button" className="ovh-btn" onClick={() => openExternal(mapUrl)}
            title="Open this area on OpenStreetMap">
            <ExternalIcon />Open map
          </button>
        )}
      </div>
      {r.notes.length > 0 && (
        <div className="ovh-notes">
          {r.notes.map(note => <span key={note}>{note}</span>)}
        </div>
      )}
    </footer>
  )
}

/** Shown while the layers load: the real header (it only needs the tool result), shimmering media. */
export function Skeleton({ r, innerRef }: { r: OverheadResult; innerRef: (el: HTMLElement | null) => void }) {
  return (
    <Shell r={r} label={`${eyebrow(r)}: ${r.place.name}, loading`} busy>
      <div className="ovh-body" ref={innerRef}>
        <div className="ovh-media">
          <div className="ovh-skel" style={{ aspectRatio: '1 / 1' }} />
        </div>
        <div className="ovh-aside">
          {r.headline ? <div className="ovh-headline">{r.headline}</div> : <div className="ovh-skel ovh-skel-line" />}
          <Chips chips={statChips(r)} focus={r.focus} />
          <div className="ovh-skel ovh-skel-line" style={{ width: '60%' }} />
        </div>
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------------------- hooks

/** True once the element comes within 300 px of the viewport, so a long chat does not pull
 *  every card's images at once. Without IntersectionObserver it is true at once. */
export function useSeen(): [(el: HTMLElement | null) => void, boolean] {
  const [el, setEl] = useState<HTMLElement | null>(null)
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (seen || !el) return
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) setSeen(true)
    }, { rootMargin: '300px' })
    io.observe(el)
    return () => io.disconnect()
  }, [el, seen])
  return [setEl, seen]
}

/** An element's content width, tracked with a ResizeObserver (the chart draws in real pixels). */
export function useWidth(initial: number): [(el: HTMLElement | null) => void, number] {
  const [el, setEl] = useState<HTMLElement | null>(null)
  const [width, setWidth] = useState(initial)
  useEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) setWidth(Math.round(w))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return [setEl, width]
}

// --------------------------------------------------------------------------------------- layers

const MAX_CACHED = 6
const cache = new Map<string, Promise<Layers>>()

/** The run's layers, fetched once per run id and kept for the last few runs: a long timelapse can
 *  be several MB, and the transcript remounts cards as it scrolls. A failure is not cached. */
export function loadLayers(rest: Rest, runId: string): Promise<Layers> {
  const hit = cache.get(runId)
  if (hit) {
    cache.delete(runId)
    cache.set(runId, hit)
    return hit
  }
  const request: Promise<Layers> = rest<unknown>(`/runs/${runId}/layers`, { timeoutMs: 60_000 }).then(value => {
    const layers = readLayers(value)
    if (!layers) throw new Error('Unexpected layers payload')
    return layers
  })
  request.catch(() => {
    if (cache.get(runId) === request) cache.delete(runId)
  })
  cache.set(runId, request)
  while (cache.size > MAX_CACHED) cache.delete(cache.keys().next().value as string)
  return request
}

export function clearLayerCache(): void {
  cache.clear()
}

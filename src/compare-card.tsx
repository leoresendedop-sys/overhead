/**
 * overhead_compare: a before/after swipe slider over the two scenes, the change overlay on the
 * after side, the stat chips and the headline.
 */

import { type KeyboardEvent, type PointerEvent, useRef, useState } from 'react'

import { fmtDate, type Layers, legendFor, osmUrl, type OverheadResult, sceneLabel, statChips } from './lib.ts'
import { ChevronsIcon, Chips, Footer, type OpenExternal, Shell } from './ui.tsx'

const clamp = (v: number) => Math.min(100, Math.max(0, v))

interface SwipeProps {
  before: string
  after: string
  overlay: string | null
  showChange: boolean
  place: string
  beforeDate: string
  afterDate: string
}

/** After clipped over before with `clip-path: inset(0 0 0 X%)`: drag anywhere on the image, or focus
 *  the handle and use the arrow keys (Shift for bigger steps, Home / End for the ends). */
function Swipe({ before, after, overlay, showChange, place, beforeDate, afterDate }: SwipeProps) {
  const [split, setSplit] = useState(50)
  const [aspect, setAspect] = useState(1)
  const [dragging, setDragging] = useState(false)
  const stage = useRef<HTMLDivElement>(null)
  const handle = useRef<HTMLDivElement>(null)

  const moveTo = (clientX: number) => {
    const box = stage.current?.getBoundingClientRect()
    if (box && box.width > 0) setSplit(clamp(((clientX - box.left) / box.width) * 100))
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // Capture is a nicety: dragging still works while the pointer stays over the image.
    }
    setDragging(true)
    moveTo(e.clientX)
    handle.current?.focus({ preventScroll: true })
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging) moveTo(e.clientX)
  }
  const stop = () => setDragging(false)

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 10 : 2
    const next: Record<string, number> = {
      ArrowLeft: split - step, ArrowDown: split - step, ArrowRight: split + step, ArrowUp: split + step,
      PageDown: split - 10, PageUp: split + 10, Home: 0, End: 100
    }
    if (!(e.key in next)) return
    e.preventDefault()
    setSplit(clamp(next[e.key]))
  }

  const clip = `inset(0 0 0 ${split}%)`
  const shown = Math.round(split)
  return (
    <div
      className="ovh-stage ovh-swipe"
      data-dragging={dragging || undefined}
      ref={stage}
      style={{ aspectRatio: String(aspect) }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
    >
      <img className="ovh-layer" src={before} alt={`${place} before, ${beforeDate}`} draggable={false}
        onLoad={e => {
          const { naturalWidth: w, naturalHeight: h } = e.currentTarget
          if (w > 0 && h > 0) setAspect(w / h)
        }} />
      <img className="ovh-layer" src={after} alt={`${place} after, ${afterDate}`} draggable={false}
        style={{ clipPath: clip }} />
      {overlay && (
        <img className="ovh-layer ovh-overlay" src={overlay} alt="" aria-hidden="true" draggable={false}
          style={{ clipPath: clip, opacity: showChange ? 1 : 0 }} />
      )}
      {/* Each label is clipped to its own side, so the divider cuts it like the image under it. */}
      <div className="ovh-side" aria-hidden="true" style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}>
        <span className="ovh-label ovh-label-before"><b>Before</b> · {beforeDate}</span>
      </div>
      <div className="ovh-side" aria-hidden="true" style={{ clipPath: clip }}>
        <span className="ovh-label ovh-label-after"><b>After</b> · {afterDate}</span>
      </div>
      <div className="ovh-divider" aria-hidden="true" style={{ left: `${split}%` }} />
      {/* The knob stays inside the frame at the ends, so the rounded stage never clips it. */}
      <div
        className="ovh-handle"
        ref={handle}
        role="slider"
        tabIndex={0}
        aria-label="Before and after divider"
        aria-orientation="horizontal"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={shown}
        aria-valuetext={`${shown}% before, ${100 - shown}% after`}
        style={{ left: `clamp(20px, ${split}%, calc(100% - 20px))` }}
        onKeyDown={onKeyDown}
      >
        <span className="ovh-knob"><ChevronsIcon /></span>
      </div>
    </div>
  )
}

export function CompareCard({ r, layers, openExternal }: {
  r: OverheadResult
  layers: Layers & { before: string; after: string }
  openExternal: OpenExternal
}) {
  const [showChange, setShowChange] = useState(false)
  const legend = legendFor(r.focus)
  const beforeDate = fmtDate(r.before?.date) || 'before'
  const afterDate = fmtDate(r.after?.date) || 'after'
  return (
    <Shell r={r} label={`Overhead comparison: ${r.place.name}`}>
      <div className="ovh-body">
        <div className="ovh-media">
          <Swipe before={layers.before} after={layers.after} overlay={layers.overlay} showChange={showChange}
            place={r.place.name} beforeDate={beforeDate} afterDate={afterDate} />
        </div>
        <div className="ovh-aside">
          {r.headline && <div className="ovh-headline">{r.headline}</div>}
          <Chips chips={statChips(r)} focus={r.focus} />
          {layers.overlay && legend.length > 0 && (
            <div className="ovh-change">
              <button type="button" className="ovh-toggle" role="switch" aria-checked={showChange}
                onClick={() => setShowChange(on => !on)}>
                <span className="ovh-track" aria-hidden="true" />
                Show change
              </button>
              <div className="ovh-legend" data-off={showChange ? undefined : ''}>
                {legend.map(item => (
                  <span key={item.label}>
                    <i className="ovh-swatch" style={{ background: item.color }} />{item.label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      <Footer r={r} scenes={[sceneLabel(r.before), sceneLabel(r.after)]} mapUrl={osmUrl(r.place.bbox)}
        openExternal={openExternal} />
    </Shell>
  )
}

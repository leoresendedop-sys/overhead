/**
 * Overhead for Hermes Desktop: draws the files the overhead_compare and overhead_timelapse tools
 * deliver (panel.png, timelapse.gif) as interactive cards instead of a still image.
 *
 * The tools keep emitting a plain `MEDIA:` line, so every other surface (CLI, TUI, messaging) shows
 * the image as before. On Desktop the tool media renderer area hands this plugin the producing call;
 * the card reads the run id from its result and loads the run's layers from this package's backend
 * (`dashboard/plugin_api.py`, GET /runs/{run_id}/layers) through `ctx.rest`. Anything it cannot draw
 * (an unparsable result, a failed request, another file) gets the built-in viewer back.
 */

import * as sdk from '@hermes/plugin-sdk'
import type { HermesPlugin, ToolMediaRendererProps } from '@hermes/plugin-sdk'
import { type ReactNode, useEffect, useState } from 'react'

import { CompareCard } from './compare-card.tsx'
import { isCardMedia, type Layers, type OverheadResult, parseResult } from './lib.ts'
import { TimelapseCard } from './timelapse-card.tsx'
import { clearLayerCache, loadLayers, type OpenExternal, type Rest, Skeleton, useSeen } from './ui.tsx'

const TOOLS: Record<string, OverheadResult['kind']> = {
  overhead_compare: 'compare',
  overhead_timelapse: 'timelapse'
}

type State = { status: 'waiting' } | { status: 'ready'; layers: Layers } | { status: 'failed' }

function OverheadCard({ initial, fallback, rest, openExternal }: {
  initial: OverheadResult
  fallback: ReactNode
  rest: Rest
  openExternal: OpenExternal
}) {
  const [ref, seen] = useSeen()
  const [state, setState] = useState<State>({ status: 'waiting' })
  const runId = initial.run_id

  useEffect(() => {
    if (!seen) return
    let live = true
    loadLayers(rest, runId).then(
      layers => live && setState({ status: 'ready', layers }),
      () => live && setState({ status: 'failed' })
    )
    return () => {
      live = false
    }
  }, [seen, rest, runId])

  if (state.status === 'failed') return <>{fallback}</>
  if (state.status === 'waiting') return <Skeleton r={initial} innerRef={ref} />

  const { layers } = state
  // result.json is the full record (it has the bbox); the tool payload is the fallback.
  const full = parseResult(layers.result)
  const r = full && full.run_id === runId ? full : initial
  if (r.kind === 'compare') {
    const { before, after } = layers
    if (!before || !after) return <>{fallback}</>
    return <CompareCard r={r} layers={{ ...layers, before, after }} openExternal={openExternal} />
  }
  if (!layers.frames.length) return <>{fallback}</>
  return <TimelapseCard r={r} layers={layers} openExternal={openExternal} />
}

/** The renderer: the card for this run's delivered panel or GIF, `fallback` for anything else.
 *  Never throws (the host also wraps it in an error boundary that shows `fallback`). */
export function renderToolMedia(props: ToolMediaRendererProps, rest: Rest, openExternal: OpenExternal): ReactNode {
  try {
    const kind = TOOLS[props.toolName]
    const r = parseResult(props.result)
    if (!kind || !r || r.kind !== kind || !isCardMedia(r, String(props.file?.path ?? ''))) return props.fallback
    return <OverheadCard key={r.run_id} initial={r} fallback={props.fallback} rest={rest} openExternal={openExternal} />
  } catch {
    return props.fallback
  }
}

const plugin: HermesPlugin = {
  id: 'overhead',
  name: 'Overhead',
  description: 'Interactive satellite cards for Overhead: a before/after swipe slider with the change overlay, '
    + 'and a timelapse player with its chart.',
  register(ctx) {
    // Older Desktops have no tool media renderer area: they keep showing the PNG / GIF.
    if (typeof sdk.TOOL_MEDIA_RENDERER_AREA !== 'string') return
    const rest: Rest = (path, opts) => ctx.rest(path, opts)
    const openExternal: OpenExternal = typeof ctx.os?.openExternal === 'function'
      ? url => {
          void ctx.os.openExternal(url).catch(() => false)
        }
      : null
    ctx.register({
      id: 'cards',
      area: sdk.TOOL_MEDIA_RENDERER_AREA,
      data: {
        tools: Object.keys(TOOLS),
        render: (props: ToolMediaRendererProps) => renderToolMedia(props, rest, openExternal)
      }
    })
    ctx.onDispose(clearLayerCache)
  }
}

export default plugin

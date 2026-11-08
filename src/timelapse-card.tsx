/**
 * overhead_timelapse: a frame player (play / pause, scrubber, date) with the series chart under it.
 * The chart marks the current frame, draws glint-flagged points hollow and shows missing periods
 * (no clear scene, so no frame) as dashed.
 */

import { useEffect, useState } from 'react'

import {
  chartCoords,
  chartGapPath,
  chartPath,
  fmtDate,
  formatValue,
  frameSeriesIndex,
  type Layers,
  metricColor,
  osmUrl,
  type OverheadResult,
  periodLabel,
  type SeriesPoint,
  shortPeriod,
  statChips
} from './lib.ts'
import { Chips, Footer, type OpenExternal, PauseIcon, PlayIcon, Shell, useWidth, WarnIcon } from './ui.tsx'

const FRAME_MS = 700 // the GIF's frame time (engine/render.py save_gif)
const METRIC_NAME: Record<string, string> = { water: 'Water area', ndvi: 'Mean NDVI', burn: 'Burned area' }
const GLINT = 'Sun glint or haze over water: likely underestimated.'

const finite = (v: number | null): v is number => typeof v === 'number' && Number.isFinite(v)

function Chart({ series, metric, current, onPick }: {
  series: SeriesPoint[]
  metric: string | undefined
  current: number
  onPick: (seriesIndex: number) => void
}) {
  const [ref, width] = useWidth(600)
  const height = 136
  const top = 20
  const bottom = 22
  const side = 6
  const w = Math.max(40, width - 2 * side)
  const h = height - top - bottom
  const values = series.map(p => p.value)
  const coords = chartCoords(values, w, h)
  const nums = values.filter(finite)
  const color = metricColor(metric)
  const oneYear = new Set(series.map(p => p.period.slice(0, 4))).size <= 1
  const every = Math.max(1, Math.ceil(series.length / Math.max(1, Math.floor(w / 46))))
  const xOf = (i: number) => (series.length === 1 ? w / 2 : (i / (series.length - 1)) * w)
  const flagged = series.some(p => p.flag && finite(p.value))
  const missing = series.some(p => !finite(p.value))
  const here = coords[current]
  const max = nums.length ? Math.max(...nums) : null
  const min = nums.length ? Math.min(...nums) : null
  return (
    <div className="ovh-chart" ref={ref}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={`${METRIC_NAME[metric ?? ''] ?? 'Value'} per period, ${nums.length} of ${series.length} with a clear scene`}>
        <g transform={`translate(${side},${top})`}>
          {max !== null && min !== null && (
            <g>
              <line x1={0} x2={w} y1={0} y2={0} stroke="var(--ovh-hairline)" />
              <line x1={0} x2={w} y1={h} y2={h} stroke="var(--ovh-hairline)" />
              <text x={0} y={-6}>{formatValue(metric, max)}</text>
              {max !== min && <text x={w} y={h - 5} textAnchor="end">{formatValue(metric, min)}</text>}
            </g>
          )}
          {series.map((p, i) => !finite(p.value) && (
            <line key={`gap-${p.period}`} x1={xOf(i)} x2={xOf(i)} y1={0} y2={h} stroke="var(--ovh-line)"
              strokeDasharray="2 3">
              <title>{`${periodLabel(p.period)}: no clear scene`}</title>
            </line>
          ))}
          <path d={chartGapPath(values, w, h)} fill="none" stroke={color} strokeOpacity={0.55} strokeWidth={1.5}
            strokeDasharray="3 4" strokeLinecap="round" />
          <path d={chartPath(values, w, h)} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round"
            strokeLinecap="round" />
          {here && (
            <line x1={here.x} x2={here.x} y1={here.y} y2={h} stroke="var(--ovh-fg3)" strokeWidth={1} />
          )}
          {coords.map((c, i) => c && (
            <circle key={`pt-${series[i].period}`} className="ovh-pt" cx={c.x} cy={c.y}
              r={i === current ? 5 : 3.5}
              fill={series[i].flag ? 'var(--ovh-surface)' : color}
              stroke={i === current ? 'var(--ovh-fg)' : color}
              strokeWidth={series[i].flag || i === current ? 1.75 : 0}
              onClick={() => onPick(i)}>
              <title>{`${fmtDate(series[i].date) || periodLabel(series[i].period)}: ${formatValue(metric, series[i].value)}${series[i].flag ? ' (sun glint)' : ''}`}</title>
            </circle>
          ))}
          {series.map((p, i) => i % every === 0 && (
            <text key={`x-${p.period}`} x={xOf(i)} y={h + 16}
              textAnchor={series.length === 1 ? 'middle' : i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle'}
              fontWeight={i === current ? 600 : undefined}>
              {shortPeriod(p.period, oneYear)}
            </text>
          ))}
        </g>
      </svg>
      {(flagged || missing) && (
        <div className="ovh-chart-key">
          {flagged && (
            <span>
              <svg aria-hidden="true" width="10" height="10"><circle cx="5" cy="5" r="3.5" fill="none" stroke={color} strokeWidth="1.5" /></svg>
              Sun glint: likely underestimated
            </span>
          )}
          {missing && (
            <span>
              <svg aria-hidden="true" width="10" height="10"><line x1="5" x2="5" y1="0" y2="10" stroke="var(--ovh-fg3)" strokeDasharray="2 2" /></svg>
              No clear scene
            </span>
          )}
        </div>
      )}
    </div>
  )
}

export function TimelapseCard({ r, layers, openExternal }: {
  r: OverheadResult
  layers: Layers
  openExternal: OpenExternal
}) {
  const series = r.series ?? []
  const frames = layers.frames
  const count = frames.length
  // Frames exist only for periods with a clear scene; if the counts disagree, show no per-frame data.
  const mapping = frameSeriesIndex(series)
  const seriesOf = (frame: number) => (mapping.length === count ? mapping[frame] : -1)
  const [frame, setFrame] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [aspect, setAspect] = useState(1)

  useEffect(() => {
    if (!playing || count < 2) return
    const timer = setInterval(() => setFrame(f => (f + 1) % count), FRAME_MS)
    return () => clearInterval(timer)
  }, [playing, count])

  const current = seriesOf(frame)
  const point: SeriesPoint | undefined = series[current]
  const date = fmtDate(point?.date) || (point ? periodLabel(point.period) : `Frame ${frame + 1}`)
  const glint = point?.flag === 'glint'
  const firstDate = fmtDate(series[mapping[0]]?.date)
  const lastDate = fmtDate(series[mapping[mapping.length - 1]]?.date)

  const pick = (seriesIndex: number) => {
    const f = mapping.length === count ? mapping.indexOf(seriesIndex) : -1
    if (f >= 0) {
      setPlaying(false)
      setFrame(f)
    }
  }

  return (
    <Shell r={r} label={`Overhead timelapse: ${r.place.name}`}>
      <div className="ovh-body">
        <div className="ovh-media">
          <div className="ovh-stage ovh-frames" style={{ aspectRatio: String(aspect) }}>
            {frames.map((src, i) => (
              <img key={i} className="ovh-layer" src={src} draggable={false} data-on={i === frame || undefined}
                alt={i === frame ? `${r.place.name}, ${date}` : ''} aria-hidden={i === frame ? undefined : true}
                onLoad={i === 0 ? e => {
                  const { naturalWidth: w, naturalHeight: h } = e.currentTarget
                  if (w > 0 && h > 0) setAspect(w / h)
                } : undefined} />
            ))}
            {glint && <span className="ovh-flag"><WarnIcon />Sun glint</span>}
          </div>
          <div className="ovh-player">
            {count > 1 && (
              <>
                <button type="button" className="ovh-play" aria-label={playing ? 'Pause timelapse' : 'Play timelapse'}
                  onClick={() => setPlaying(p => !p)}>
                  {playing ? <PauseIcon /> : <PlayIcon />}
                </button>
                <input type="range" className="ovh-range" min={0} max={count - 1} step={1} value={frame}
                  aria-label="Timelapse frame" aria-valuetext={`${date}, frame ${frame + 1} of ${count}`}
                  onChange={e => {
                    setPlaying(false)
                    setFrame(Number(e.currentTarget.value))
                  }} />
              </>
            )}
            <span className="ovh-frame-date">{date} · {frame + 1}/{count}</span>
          </div>
        </div>
        <div className="ovh-aside">
          {point && (
            <div className="ovh-readout">
              <span className="ovh-readout-label">{METRIC_NAME[r.metric ?? ''] ?? 'Value'}</span>
              <span className="ovh-readout-value">{formatValue(r.metric, point.value)}</span>
              {point.clear !== null && <span className="ovh-readout-sub">{Math.round(point.clear * 100)}% clear</span>}
            </div>
          )}
          {glint && <div className="ovh-warn"><WarnIcon />{GLINT}</div>}
          {r.headline && <div className="ovh-headline">{r.headline}</div>}
          <Chips chips={statChips(r)} />
        </div>
      </div>
      {series.length > 1 && <Chart series={series} metric={r.metric} current={current} onPick={pick} />}
      <Footer r={r} scenes={[`${count} frame${count === 1 ? '' : 's'}`, firstDate && lastDate && firstDate !== lastDate ? `${firstDate} – ${lastDate}` : firstDate]}
        mapUrl={osmUrl(r.place.bbox)} openExternal={openExternal} />
    </Shell>
  )
}

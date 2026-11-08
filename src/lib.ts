/**
 * Pure helpers for the Overhead Desktop card: read a tool result, format figures the way the engine's
 * headline does, and lay out the timelapse chart. No React, no DOM, so `node --test` runs them as is.
 */

/** Same pattern as engine/runs.py RUN_ID_RE. */
export const RUN_ID_RE = /^(compare|timelapse)-\d{8}-[0-9a-f]{6}$/

export type Kind = 'compare' | 'timelapse'
export type Tone = 'loss' | 'gain' | 'neutral'

export interface Chip {
  label: string
  tone: Tone
}

export interface Place {
  name: string
  full_name?: string
  /** [minlon, minlat, maxlon, maxlat]; result.json has it, the tool payload does not. */
  bbox?: number[]
  area_km2?: number
}

export interface Side {
  requested?: string
  date: string | null
  clear_fraction?: number
  composite?: boolean
  scenes?: string[]
}

export interface SeriesPoint {
  period: string
  date: string | null
  value: number | null
  clear: number | null
  /** "glint": sun glint or smoke haze over water, the value is likely underestimated. */
  flag?: string
}

export type Stats = Partial<Record<'burn' | 'vegetation' | 'water', Record<string, unknown>>>

export interface OverheadResult {
  run_id: string
  kind: Kind
  place: Place
  headline: string
  notes: string[]
  focus?: string
  metric?: string
  confidence?: string
  valid_fraction?: number
  resolution_m?: number
  stats?: Stats
  before?: Side
  after?: Side
  series?: SeriesPoint[]
}

export interface LegendItem {
  label: string
  color: string
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MINUS = '−'
const DASH = '–'

// ------------------------------------------------------------------------------------- reading

type Dict = Record<string, unknown>

const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)

/** JSON text, or the object inside text that carries something after it (an appended hint). */
function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    const start = text.indexOf('{')
    const end = text.lastIndexOf('}')
    if (start < 0 || end <= start) return null
    try {
      return JSON.parse(text.slice(start, end + 1))
    } catch {
      return null
    }
  }
}

function firstText(parts: readonly unknown[]): string | null {
  for (const part of parts) {
    if (isDict(part) && part.type === 'text' && typeof part.text === 'string') return part.text
  }
  return null
}

/** The result object inside whatever shape the host hands over: an object, a JSON string, the
 *  `_multimodal` envelope (first text part, else `text_summary`) or a bare content-part list. */
function unwrap(value: unknown, depth: number): Dict | null {
  if (depth > 4) return null
  if (typeof value === 'string') return unwrap(parseJson(value), depth + 1)
  if (Array.isArray(value)) {
    const text = firstText(value)
    return text === null ? null : unwrap(text, depth + 1)
  }
  if (!isDict(value)) return null
  if (value._multimodal === true) {
    const text = Array.isArray(value.content) ? firstText(value.content) : null
    const inner = text === null ? null : unwrap(text, depth + 1)
    if (inner) return inner
    return typeof value.text_summary === 'string' ? unwrap(value.text_summary, depth + 1) : null
  }
  return value
}

function validBbox(v: unknown): v is number[] {
  if (!Array.isArray(v) || v.length !== 4 || !v.every(x => num(x) !== undefined)) return false
  const [minlon, minlat, maxlon, maxlat] = v as number[]
  const lonOk = (x: number) => x >= -180 && x <= 180
  const latOk = (y: number) => y >= -90 && y <= 90
  return lonOk(minlon) && lonOk(maxlon) && latOk(minlat) && latOk(maxlat) && minlon <= maxlon && minlat <= maxlat
}

function readPlace(v: unknown): Place {
  if (!isDict(v)) return { name: 'Selected area' }
  const place: Place = { name: str(v.name).trim() || str(v.full_name).trim() || 'Selected area' }
  if (str(v.full_name)) place.full_name = str(v.full_name)
  if (validBbox(v.bbox)) place.bbox = [...v.bbox]
  if (num(v.area_km2) !== undefined) place.area_km2 = num(v.area_km2)
  return place
}

function readSide(v: unknown): Side | undefined {
  if (!isDict(v)) return undefined
  const side: Side = { date: str(v.date) || null }
  if (str(v.requested)) side.requested = str(v.requested)
  if (num(v.clear_fraction) !== undefined) side.clear_fraction = num(v.clear_fraction)
  if (typeof v.composite === 'boolean') side.composite = v.composite
  if (Array.isArray(v.scenes)) side.scenes = v.scenes.filter((s): s is string => typeof s === 'string')
  return side
}

function readPoint(v: Dict): SeriesPoint {
  const point: SeriesPoint = {
    period: str(v.period),
    date: str(v.date) || null,
    value: num(v.value) ?? null,
    clear: num(v.clear) ?? null
  }
  if (str(v.flag)) point.flag = str(v.flag)
  return point
}

/** The run a compare / timelapse tool result describes, or null (error, junk, bad run_id). Never throws. */
export function parseResult(result: unknown): OverheadResult | null {
  try {
    const o = unwrap(result, 0)
    if (!o) return null
    if (o.error !== undefined && o.error !== null && o.error !== false && o.error !== '') return null
    const runId = o.run_id
    if (typeof runId !== 'string' || !RUN_ID_RE.test(runId)) return null
    const r: OverheadResult = {
      run_id: runId,
      kind: runId.startsWith('timelapse-') ? 'timelapse' : 'compare',
      place: readPlace(o.place),
      headline: str(o.headline),
      notes: Array.isArray(o.notes) ? o.notes.filter((n): n is string => typeof n === 'string') : []
    }
    for (const key of ['focus', 'metric', 'confidence'] as const) if (str(o[key])) r[key] = str(o[key])
    for (const key of ['valid_fraction', 'resolution_m'] as const) {
      if (num(o[key]) !== undefined) r[key] = num(o[key])
    }
    if (isDict(o.stats)) r.stats = o.stats as Stats
    const before = readSide(o.before)
    const after = readSide(o.after)
    if (before) r.before = before
    if (after) r.after = after
    if (Array.isArray(o.series)) r.series = o.series.filter(isDict).map(readPoint)
    return r
  } catch {
    return null
  }
}

/** True for the one file the card replaces: this run's panel.png (compare) or timelapse.gif. Any
 *  other file the model names (before.jpg, a frame) keeps the built-in viewer. */
export function isCardMedia(r: OverheadResult, path: string): boolean {
  const parts = path.replace(/^file:\/\//i, '').replace(/\\/g, '/').split('/').filter(Boolean)
  const want = r.kind === 'compare' ? 'panel.png' : 'timelapse.gif'
  return parts.length >= 2 && parts[parts.length - 1] === want && parts[parts.length - 2] === r.run_id
}

// ---------------------------------------------------------------------------------- formatting

/** Python's round() / format(): the exact binary value, ties to even, so the card's figures match
 *  the engine's headline. */
function roundHalfEven(x: number, digits: number): number {
  const [whole, frac = ''] = Math.abs(x).toFixed(digits + 20).split('.')
  const kept = Number(whole + frac.slice(0, digits))
  const rest = frac.slice(digits)
  const tie = rest[0] === '5' && /^0*$/.test(rest.slice(1))
  const up = rest[0] > '5' || (rest[0] === '5' && !tie) || (tie && kept % 2 === 1)
  return (Math.sign(x) || 1) * (kept + (up ? 1 : 0)) / 10 ** digits
}

const group = (n: number) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',')

/** Like engine/report.py fmt_ha without the unit: whole numbers from 10 up, one decimal below. */
function formatNumber(n: number): string {
  const a = Math.abs(n)
  const sign = n < 0 ? MINUS : ''
  if (a === 0) return '0'
  return a >= 10 ? sign + group(roundHalfEven(a, 0)) : sign + roundHalfEven(a, 1).toFixed(1)
}

/** "4,120 ha"; "< 0.1 ha" for a tiny non-zero area. */
export function formatHa(n: number): string {
  if (!Number.isFinite(n)) return DASH
  if (n !== 0 && Math.abs(n) < 0.1) return '< 0.1 ha'
  return `${formatNumber(n)} ha`
}

/** A timelapse value: hectares for water and burn, two decimals for NDVI. */
export function formatValue(metric: string | undefined, v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return DASH
  return metric === 'ndvi' ? v.toFixed(2) : formatHa(v)
}

/** "2024-12-18" -> "18 Dec 2024" (engine/report.py fmt_date); "" when it is not a date. */
export function fmtDate(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  if (!m) return ''
  const month = Number(m[2])
  const day = Number(m[3])
  if (month < 1 || month > 12 || day < 1 || day > 31) return ''
  return `${day} ${MONTHS[month - 1]} ${m[1]}`
}

/** A timelapse period: "2024-06" -> "Jun 2024", "2024-Q3" -> "Q3 2024", "2024" stays. */
export function periodLabel(period: string): string {
  const month = /^(\d{4})-(\d{2})$/.exec(period)
  if (month && Number(month[2]) >= 1 && Number(month[2]) <= 12) return `${MONTHS[Number(month[2]) - 1]} ${month[1]}`
  const quarter = /^(\d{4})-Q([1-4])$/.exec(period)
  if (quarter) return `Q${quarter[2]} ${quarter[1]}`
  return period
}

/** A chart tick: "Jun" (or "Jun ’24" when the series spans years), "Q3 ’24", "2024". */
export function shortPeriod(period: string, oneYear: boolean): string {
  const year = (y: string) => (oneYear ? '' : ` ’${y.slice(2)}`)
  const month = /^(\d{4})-(\d{2})$/.exec(period)
  if (month && Number(month[2]) >= 1 && Number(month[2]) <= 12) return MONTHS[Number(month[2]) - 1] + year(month[1])
  const quarter = /^(\d{4})-Q([1-4])$/.exec(period)
  if (quarter) return `Q${quarter[2]}${year(quarter[1])}`
  return period
}

/** Footer provenance for one side: "S2B 18 Dec 2024", or "31 Jul 2019 (composite)". */
export function sceneLabel(side: Side | undefined): string {
  const date = fmtDate(side?.date)
  if (!side || !date) return ''
  if (side.composite) return `${date} (composite)`
  const platforms = new Set((side.scenes ?? []).map(id => /^(S2[A-D])_/.exec(id)?.[1]).filter(Boolean))
  return platforms.size === 1 ? `${[...platforms][0]} ${date}` : date
}

/** Header dates: the scene dates of a compare, the first and last period of a timelapse. */
export function dateRange(r: OverheadResult): string {
  if (r.kind === 'compare') {
    const a = fmtDate(r.before?.date ?? r.before?.requested)
    const b = fmtDate(r.after?.date ?? r.after?.requested)
    return a && b ? `${a} → ${b}` : a || b
  }
  const series = r.series ?? []
  if (!series.length) return ''
  const first = periodLabel(series[0].period)
  const last = periodLabel(series[series.length - 1].period)
  return first === last ? first : `${first} → ${last}`
}

const FOCUS_LABEL: Record<string, string> = {
  burn: 'Burned area',
  vegetation: 'Vegetation change',
  water: 'Water change'
}
const METRIC_LABEL: Record<string, string> = {
  water: 'Water area',
  ndvi: 'Vegetation (NDVI)',
  burn: 'Burned area'
}

function stepOf(series: readonly SeriesPoint[]): string {
  const period = series[0]?.period ?? ''
  if (/^\d{4}-\d{2}$/.test(period)) return 'monthly'
  if (/^\d{4}-Q[1-4]$/.test(period)) return 'quarterly'
  if (/^\d{4}$/.test(period)) return 'yearly'
  return ''
}

/** The small label above the place name. */
export function eyebrow(r: OverheadResult): string {
  if (r.kind === 'compare') return FOCUS_LABEL[r.focus ?? ''] ?? 'Change'
  const label = METRIC_LABEL[r.metric ?? ''] ?? 'Timelapse'
  const step = stepOf(r.series ?? [])
  return step ? `${label} · ${step}` : label
}

export function confidenceLabel(confidence: string | undefined): string {
  if (!confidence) return ''
  return `${confidence[0].toUpperCase()}${confidence.slice(1)} confidence`
}

/** Overlay colours and labels, the same as the PNG panel (engine/render.py OVERLAY_COLORS). */
const LEGEND: Record<string, LegendItem[]> = {
  burn: [
    { label: 'Low severity', color: '#F5D90A' },
    { label: 'Moderate severity', color: '#F76B15' },
    { label: 'High severity', color: '#E5484D' }
  ],
  vegetation: [
    { label: 'Vegetation loss', color: '#E5484D' },
    { label: 'Vegetation gain', color: '#30A46C' }
  ],
  water: [
    { label: 'Water gained', color: '#0090FF' },
    { label: 'Water lost', color: '#F76B15' }
  ]
}

export function legendFor(focus: string | undefined): LegendItem[] {
  return (LEGEND[focus ?? ''] ?? []).map(item => ({ ...item }))
}

const METRIC_COLOR: Record<string, string> = { water: '#0090FF', ndvi: '#30A46C', burn: '#F76B15' }

/** Line colour of the timelapse chart, from the overlay palette. */
export function metricColor(metric: string | undefined): string {
  return METRIC_COLOR[metric ?? ''] ?? '#0090FF'
}

// ---------------------------------------------------------------------------------- stat chips

function focusChips(r: OverheadResult): Chip[] {
  const stats = r.stats ?? {}
  if (r.focus === 'burn' && stats.burn) {
    const b = stats.burn
    const high = num(b.high_ha)
    const burned = num(b.burned_ha) ?? (high !== undefined ? (num(b.moderate_ha) ?? 0) + high : undefined)
    if (burned === undefined) return []
    if (burned <= 0) return [{ label: 'No burn detected', tone: 'neutral' }]
    const chips: Chip[] = [{ label: `${formatHa(burned)} burned`, tone: 'loss' }]
    if (high !== undefined && high > 0) chips.push({ label: `${formatHa(high)} high severity`, tone: 'loss' })
    return chips
  }
  if (r.focus === 'vegetation' && stats.vegetation) {
    const lost = num(stats.vegetation.lost_ha)
    const gained = num(stats.vegetation.gained_ha)
    const chips: Chip[] = []
    if (lost !== undefined && lost > 0) chips.push({ label: `${formatHa(lost)} lost`, tone: 'loss' })
    if (gained !== undefined && gained > 0) chips.push({ label: `${formatHa(gained)} gained`, tone: 'gain' })
    if (!chips.length && (lost !== undefined || gained !== undefined)) {
      chips.push({ label: 'No vegetation change', tone: 'neutral' })
    }
    return chips
  }
  if (r.focus === 'water' && stats.water) {
    const w = stats.water
    const net = num(w.net_ha)
    const before = num(w.before_ha)
    const after = num(w.after_ha)
    const chips: Chip[] = []
    if (net !== undefined) {
      chips.push(Math.abs(net) < 0.1
        ? { label: 'No net water change', tone: 'neutral' }
        : { label: `${net > 0 ? '+' : ''}${formatHa(net)} water`, tone: net > 0 ? 'gain' : 'loss' })
    }
    if (before !== undefined && after !== undefined) {
      chips.push({ label: `${formatNumber(before)} → ${formatNumber(after)} ha`, tone: 'neutral' })
    }
    return chips
  }
  return []
}

/** At most 4 chips: the focus figures first, then "<pct>% clear" and "<res> m". */
export function statChips(r: OverheadResult): Chip[] {
  const chips: Chip[] = []
  if (r.kind === 'compare') {
    chips.push(...focusChips(r))
    if (r.valid_fraction !== undefined) {
      chips.push({ label: `${Math.round(r.valid_fraction * 100)}% clear`, tone: 'neutral' })
    }
  } else if (r.series?.length) {
    const clear = r.series.filter(p => p.date !== null).length
    chips.push({ label: `${clear} of ${r.series.length} periods clear`, tone: 'neutral' })
  }
  if (r.resolution_m !== undefined) chips.push({ label: `${r.resolution_m} m`, tone: 'neutral' })
  return chips.slice(0, 4)
}

// --------------------------------------------------------------------------------------- chart

export interface Point {
  x: number
  y: number
}

const r2 = (v: number) => Math.round(v * 100) / 100 + 0 // + 0 turns -0 into 0

/** Each value's position in a w × h box (y down, the series' min at the bottom); null stays null. */
export function chartCoords(values: readonly (number | null)[], w: number, h: number): (Point | null)[] {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  if (!nums.length) return values.map(() => null)
  const min = Math.min(...nums)
  const span = Math.max(...nums) - min
  const n = values.length
  return values.map((v, i) =>
    typeof v === 'number' && Number.isFinite(v)
      ? { x: r2(n === 1 ? w / 2 : (i / (n - 1)) * w), y: r2(span === 0 ? h / 2 : h - ((v - min) / span) * h) }
      : null)
}

/** SVG path of the series, lifted at every null so a missing period leaves a gap. */
export function chartPath(values: readonly (number | null)[], w: number, h: number): string {
  const out: string[] = []
  let penDown = false
  for (const p of chartCoords(values, w, h)) {
    if (!p) {
      penDown = false
      continue
    }
    out.push(`${penDown ? 'L' : 'M'}${p.x},${p.y}`)
    penDown = true
  }
  return out.join(' ')
}

/** Straight bridges across interior gaps, drawn dashed under the line. */
export function chartGapPath(values: readonly (number | null)[], w: number, h: number): string {
  const out: string[] = []
  let last: { i: number; p: Point } | null = null
  chartCoords(values, w, h).forEach((p, i) => {
    if (!p) return
    if (last && i > last.i + 1) out.push(`M${last.p.x},${last.p.y} L${p.x},${p.y}`)
    last = { i, p }
  })
  return out.join(' ')
}

/** Series index of each timelapse frame: frames exist only for periods that had a clear scene. */
export function frameSeriesIndex(series: readonly SeriesPoint[]): number[] {
  const out: number[] = []
  series.forEach((p, i) => {
    if (p.date !== null) out.push(i)
  })
  return out
}

// ---------------------------------------------------------------------------------------- layers

/** GET /runs/{run_id}/layers (dashboard/plugin_api.py): images as data URLs, plus result.json. */
export interface Layers {
  kind: string
  before: string | null
  after: string | null
  overlay: string | null
  frames: string[]
  chart: string | null
  result: unknown
}

const DATA_IMAGE = /^data:image\/(?:jpeg|png|gif|webp);base64,/

const image = (v: unknown): string | null => (typeof v === 'string' && DATA_IMAGE.test(v) ? v : null)

/** The layers payload with every image checked to be an image data URL; null when it is not an object. */
export function readLayers(value: unknown): Layers | null {
  if (!isDict(value)) return null
  return {
    kind: str(value.kind),
    before: image(value.before),
    after: image(value.after),
    overlay: image(value.overlay),
    frames: Array.isArray(value.frames) ? value.frames.map(image).filter((f): f is string => f !== null) : [],
    chart: image(value.chart),
    result: value.result ?? null
  }
}

// ------------------------------------------------------------------------------------------ map

/** OpenStreetMap framed on the bbox; "" when the bbox is not usable. */
export function osmUrl(bbox: number[] | undefined): string {
  if (!validBbox(bbox)) return ''
  const [minlon, minlat, maxlon, maxlat] = bbox.map(v => Number(v.toFixed(5)))
  return `https://www.openstreetmap.org/?minlon=${minlon}&minlat=${minlat}&maxlon=${maxlon}&maxlat=${maxlat}`
}

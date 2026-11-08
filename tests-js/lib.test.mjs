// Unit tests for the Desktop card's pure helpers (src/lib.ts). Run: npm test
// Fixtures are copies of real result.json files with the home directory replaced by /home/user.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import {
  chartCoords,
  chartGapPath,
  chartPath,
  confidenceLabel,
  dateRange,
  eyebrow,
  fmtDate,
  formatHa,
  formatValue,
  frameSeriesIndex,
  isCardMedia,
  legendFor,
  metricColor,
  osmUrl,
  parseResult,
  periodLabel,
  readLayers,
  sceneLabel,
  shortPeriod,
  statChips
} from '../src/lib.ts'

const fixture = name => JSON.parse(readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), 'utf8'))
const ALTADENA = fixture('compare-altadena')
const NOVO = fixture('compare-novo-progresso')
const MANAUS = fixture('timelapse-manaus')

// The compare payload the overhead_compare handler returns (handlers.py): a subset of result.json
// plus the deliver line. Its place has no bbox.
function toolPayload(r) {
  const panel = r.files.panel
  return {
    run_id: r.run_id,
    place: { name: r.place.name, full_name: r.place.full_name, area_km2: r.place.area_km2 },
    headline: r.headline,
    focus: r.focus,
    stats: r.stats,
    confidence: r.confidence,
    valid_fraction: r.valid_fraction,
    resolution_m: r.resolution_m,
    before: r.before,
    after: r.after,
    notes: r.notes,
    files: r.files,
    deliver: `MEDIA:${panel}`,
    instruction: `End your reply with this exact line: MEDIA:${panel}`
  }
}

// ---------------------------------------------------------------------------------- parseResult

test('parseResult reads a result object', () => {
  const r = parseResult(ALTADENA)
  assert.equal(r.run_id, 'compare-20261108-1fbafb')
  assert.equal(r.kind, 'compare')
  assert.equal(r.place.name, 'Altadena, California')
  assert.deepEqual(r.place.bbox, ALTADENA.place.bbox)
  assert.equal(r.focus, 'burn')
  assert.equal(r.before.date, '2024-12-18')
  assert.deepEqual(r.notes, ALTADENA.notes)
})

test('parseResult reads a JSON string (the tool result as returned)', () => {
  const r = parseResult(JSON.stringify(toolPayload(ALTADENA)))
  assert.equal(r.run_id, 'compare-20261108-1fbafb')
  assert.equal(r.headline, ALTADENA.headline)
  assert.equal(r.place.bbox, undefined)
})

test('parseResult reads the first text part of a _multimodal envelope', () => {
  const text = JSON.stringify(toolPayload(NOVO))
  const envelope = {
    _multimodal: true,
    content: [
      { type: 'image_url', image_url: { url: 'data:image/jpeg;base64,AAAA' } },
      { type: 'text', text },
      { type: 'text', text: 'not json' }
    ],
    text_summary: text
  }
  const r = parseResult(envelope)
  assert.equal(r.run_id, 'compare-20261108-2b18ca')
  assert.equal(r.focus, 'vegetation')
})

test('parseResult falls back to text_summary, and reads a bare content-part array', () => {
  const text = JSON.stringify(toolPayload(NOVO))
  assert.equal(parseResult({ _multimodal: true, content: [], text_summary: text }).run_id, NOVO.run_id)
  assert.equal(parseResult([{ type: 'text', text }]).run_id, NOVO.run_id)
})

test('parseResult tolerates a hint appended after the JSON text', () => {
  const r = parseResult(`${JSON.stringify(MANAUS)}\n[Files are under the run folder]`)
  assert.equal(r.run_id, 'timelapse-20261108-27e5ae')
})

test('parseResult reads a timelapse result', () => {
  const r = parseResult(MANAUS)
  assert.equal(r.kind, 'timelapse')
  assert.equal(r.metric, 'water')
  assert.equal(r.series.length, 7)
  assert.equal(r.series[4].flag, 'glint')
  assert.equal(r.series[0].value, null)
})

test('parseResult takes the kind from the run_id', () => {
  const { kind, ...rest } = ALTADENA
  assert.equal(parseResult(rest).kind, 'compare')
  assert.equal(parseResult({ ...MANAUS, kind: 'compare' }).kind, 'timelapse')
})

test('parseResult rejects a missing or malformed run_id', () => {
  for (const run_id of [undefined, '', '../etc/passwd', 'compare-1-abc', 'scenes-20261108-1fbafb',
    'compare-20261108-1FBAFB', 'compare-20261108-1fbafb/../x', 42]) {
    assert.equal(parseResult({ ...ALTADENA, run_id }), null, String(run_id))
  }
})

test('parseResult rejects null, junk and error payloads', () => {
  for (const value of [null, undefined, 42, true, '', 'not json', '{"run_id":', [], {}, { _multimodal: true }]) {
    assert.equal(parseResult(value), null, JSON.stringify(value))
  }
  const error = { error: { type: 'no_clear_scene', message: 'No clear image.' } }
  assert.equal(parseResult(error), null)
  assert.equal(parseResult({ ...ALTADENA, error: { type: 'internal', message: 'x' } }), null)
})

test('parseResult normalises odd fields instead of throwing', () => {
  const r = parseResult({ run_id: 'compare-20261108-abcdef', notes: 'one note', place: 'Somewhere', headline: 7 })
  assert.deepEqual(r.notes, [])
  assert.equal(r.place.name, 'Selected area')
  assert.equal(r.headline, '')
  assert.equal(parseResult({ run_id: 'compare-20261108-abcdef', place: { full_name: 'Full Name' } }).place.name,
    'Full Name')
})

// --------------------------------------------------------------------------------------- format

test('formatHa rounds like the engine headline', () => {
  assert.equal(formatHa(4120.4), '4,120 ha')
  assert.equal(formatHa(0.04), '< 0.1 ha')
  assert.equal(formatHa(0), '0 ha')
  assert.equal(formatHa(5.45), '5.5 ha')
  assert.equal(formatHa(3449.44), '3,449 ha')
  assert.equal(formatHa(910.5), '910 ha') // half to even, like Python's round()
  assert.equal(formatHa(911.5), '912 ha')
  assert.equal(formatHa(1234567), '1,234,567 ha')
  assert.equal(formatHa(-3475.09), '−3,475 ha')
  assert.equal(formatHa(Number.NaN), '–')
})

test('fmtDate, periodLabel and dateRange', () => {
  assert.equal(fmtDate('2024-12-18'), '18 Dec 2024')
  assert.equal(fmtDate('2025-02-01'), '1 Feb 2025')
  assert.equal(fmtDate('2025-13-01'), '')
  assert.equal(fmtDate(null), '')
  assert.equal(periodLabel('2024-06'), 'Jun 2024')
  assert.equal(periodLabel('2024-Q3'), 'Q3 2024')
  assert.equal(periodLabel('2024'), '2024')
  assert.equal(dateRange(parseResult(ALTADENA)), '18 Dec 2024 → 21 Feb 2025')
  assert.equal(dateRange(parseResult(MANAUS)), 'Jun 2024 → Dec 2024')
})

test('formatValue per metric', () => {
  assert.equal(formatValue('water', 22165.04), '22,165 ha')
  assert.equal(formatValue('burn', 0.04), '< 0.1 ha')
  assert.equal(formatValue('ndvi', 0.6234), '0.62')
  assert.equal(formatValue('water', null), '–')
})

test('eyebrow, confidenceLabel and legendFor', () => {
  assert.equal(eyebrow(parseResult(ALTADENA)), 'Burned area')
  assert.equal(eyebrow(parseResult(NOVO)), 'Vegetation change')
  assert.equal(eyebrow(parseResult(MANAUS)), 'Water area · monthly')
  assert.equal(confidenceLabel('high'), 'High confidence')
  assert.equal(confidenceLabel('low'), 'Low confidence')
  assert.equal(confidenceLabel(undefined), '')
  // Same colours as the PNG panel (engine/render.py OVERLAY_COLORS).
  assert.deepEqual(legendFor('burn'), [
    { label: 'Low severity', color: '#F5D90A' },
    { label: 'Moderate severity', color: '#F76B15' },
    { label: 'High severity', color: '#E5484D' }
  ])
  assert.deepEqual(legendFor('vegetation'), [
    { label: 'Vegetation loss', color: '#E5484D' },
    { label: 'Vegetation gain', color: '#30A46C' }
  ])
  assert.deepEqual(legendFor('water'), [
    { label: 'Water gained', color: '#0090FF' },
    { label: 'Water lost', color: '#F76B15' }
  ])
  assert.deepEqual(legendFor('nope'), [])
})

// ------------------------------------------------------------------------------------ statChips

test('statChips: burn leads with burned and high-severity hectares', () => {
  assert.deepEqual(statChips(parseResult(ALTADENA)), [
    { label: '3,449 ha burned', tone: 'loss' },
    { label: '910 ha high severity', tone: 'loss' },
    { label: '100% clear', tone: 'neutral' },
    { label: '10 m', tone: 'neutral' }
  ])
})

test('statChips: vegetation leads with lost and gained hectares', () => {
  assert.deepEqual(statChips(parseResult(NOVO)), [
    { label: '3,718 ha lost', tone: 'loss' },
    { label: '243 ha gained', tone: 'gain' },
    { label: '98% clear', tone: 'neutral' },
    { label: '10 m', tone: 'neutral' }
  ])
})

test('statChips: water leads with the net change, then before → after', () => {
  const r = parseResult({ ...ALTADENA, focus: 'water' })
  assert.deepEqual(statChips(r), [
    { label: '+30 ha water', tone: 'gain' },
    { label: '165 → 195 ha', tone: 'neutral' },
    { label: '100% clear', tone: 'neutral' },
    { label: '10 m', tone: 'neutral' }
  ])
  const drop = { ...ALTADENA.stats, water: { ...ALTADENA.stats.water, net_ha: -1200.4 } }
  assert.deepEqual(statChips(parseResult({ ...ALTADENA, focus: 'water', stats: drop }))[0],
    { label: '−1,200 ha water', tone: 'loss' })
})

test('statChips: zero figures are not shown as chips, and there are never more than 4', () => {
  const noHigh = { ...ALTADENA.stats, burn: { ...ALTADENA.stats.burn, high_ha: 0 } }
  assert.deepEqual(statChips(parseResult({ ...ALTADENA, stats: noHigh })).map(c => c.label),
    ['3,449 ha burned', '100% clear', '10 m'])
  const none = { ...ALTADENA.stats, burn: { low_ha: 3, moderate_ha: 0, high_ha: 0, burned_ha: 0 } }
  assert.deepEqual(statChips(parseResult({ ...ALTADENA, stats: none }))[0], { label: 'No burn detected', tone: 'neutral' })
  for (const r of [ALTADENA, NOVO, MANAUS]) assert.ok(statChips(parseResult(r)).length <= 4)
})

test('statChips: missing stats leave the clear and resolution chips', () => {
  const r = parseResult({ run_id: 'compare-20261108-abcdef', valid_fraction: 0.734, resolution_m: 20 })
  assert.deepEqual(statChips(r), [
    { label: '73% clear', tone: 'neutral' },
    { label: '20 m', tone: 'neutral' }
  ])
})

test('statChips: timelapse counts the clear periods', () => {
  assert.deepEqual(statChips(parseResult(MANAUS)), [
    { label: '5 of 7 periods clear', tone: 'neutral' },
    { label: '20 m', tone: 'neutral' }
  ])
})

// ---------------------------------------------------------------------------------------- chart

test('chartPath breaks the line at nulls', () => {
  const path = chartPath([1, null, 3], 100, 50)
  assert.equal(path.match(/M/g).length, 2)
  assert.equal(path, 'M0,50 M100,0')
  const full = chartPath([1, 2, 3], 100, 50)
  assert.equal(full, 'M0,50 L50,25 L100,0')
  assert.equal(chartPath([null, null], 100, 50), '')
  assert.equal(chartPath([], 100, 50), '')
})

test('chartPath centres a flat or single-point series', () => {
  assert.equal(chartPath([5], 100, 50), 'M50,25')
  assert.equal(chartPath([2, 2], 100, 50), 'M0,25 L100,25')
})

test('chartCoords keeps nulls in place', () => {
  assert.deepEqual(chartCoords([null, 1, 3], 100, 50), [null, { x: 50, y: 50 }, { x: 100, y: 0 }])
})

test('chartGapPath bridges interior gaps only', () => {
  assert.equal(chartGapPath([1, null, 3], 100, 50), 'M0,50 L100,0')
  assert.equal(chartGapPath([null, 1, 2, null], 90, 30), '')
  assert.equal(chartGapPath([1, null, null, 2, 3, null, 1], 60, 20), 'M0,20 L30,10 M40,0 L60,20')
})

test('frameSeriesIndex maps frames to the series points that have a scene', () => {
  assert.deepEqual(frameSeriesIndex(parseResult(MANAUS).series), [1, 2, 3, 4, 5])
  assert.deepEqual(frameSeriesIndex([]), [])
})

// --------------------------------------------------------------------------------------- osmUrl

test('osmUrl builds an OpenStreetMap link for the bbox', () => {
  const url = osmUrl([-118.2, 34.1, -118.0, 34.3])
  assert.ok(url.startsWith('https://www.openstreetmap.org/?'), url)
  assert.ok(url.includes('minlon=-118.2'), url)
  assert.ok(url.includes('minlat=34.1'), url)
  assert.ok(url.includes('maxlon=-118'), url)
  assert.ok(url.includes('maxlat=34.3'), url)
  assert.ok(osmUrl(ALTADENA.place.bbox).includes('minlon=-118.17524&'))
  for (const bad of [undefined, [], [1, 2, 3], [1, 2, 3, 'x'], [Number.NaN, 0, 1, 1], [0, 0, 200, 1]]) {
    assert.equal(osmUrl(bad), '', JSON.stringify(bad))
  }
})

// ---------------------------------------------------------------------------------- isCardMedia

test('isCardMedia draws the card only for the delivered panel or GIF of this run', () => {
  const compare = parseResult(ALTADENA)
  assert.equal(isCardMedia(compare, ALTADENA.files.panel), true)
  assert.equal(isCardMedia(compare, `file://${ALTADENA.files.panel}`), true)
  assert.equal(isCardMedia(compare, ALTADENA.files.panel.replaceAll('/', '\\')), true)
  assert.equal(isCardMedia(compare, ALTADENA.files.before), false)
  assert.equal(isCardMedia(compare, NOVO.files.panel), false)
  const timelapse = parseResult(MANAUS)
  assert.equal(isCardMedia(timelapse, MANAUS.files.gif), true)
  assert.equal(isCardMedia(timelapse, MANAUS.files.frames[0]), false)
  assert.equal(isCardMedia(timelapse, MANAUS.files.chart), false)
  assert.equal(isCardMedia(compare, ''), false)
})

// ------------------------------------------------------------------------------- footer, chart

test('sceneLabel names the satellite and the date, and marks composites', () => {
  const r = parseResult(ALTADENA)
  assert.equal(sceneLabel(r.before), 'S2B 18 Dec 2024')
  assert.equal(sceneLabel(r.after), 'S2C 21 Feb 2025')
  assert.equal(sceneLabel({ date: '2019-07-31', scenes: ['S2A_x_L2A', 'S2B_y_L2A'], composite: true }),
    '31 Jul 2019 (composite)')
  assert.equal(sceneLabel({ date: null, requested: '2024-01-15' }), '')
  assert.equal(sceneLabel(undefined), '')
})

test('shortPeriod drops the year when the series stays in one year', () => {
  assert.equal(shortPeriod('2024-06', true), 'Jun')
  assert.equal(shortPeriod('2024-06', false), 'Jun ’24')
  assert.equal(shortPeriod('2024-Q3', false), 'Q3 ’24')
  assert.equal(shortPeriod('2024', true), '2024')
})

test('metricColor uses the overlay palette', () => {
  assert.equal(metricColor('water'), '#0090FF')
  assert.equal(metricColor('ndvi'), '#30A46C')
  assert.equal(metricColor('burn'), '#F76B15')
})

// ---------------------------------------------------------------------------------- readLayers

test('readLayers keeps image data URLs and drops anything else', () => {
  const jpeg = 'data:image/jpeg;base64,/9j/4AAQ'
  const png = 'data:image/png;base64,iVBORw0KGgo='
  const layers = readLayers({
    kind: 'compare', before: jpeg, after: jpeg, overlay: png, frames: [jpeg, 'javascript:alert(1)', 7],
    chart: 'https://example.com/x.png', result: ALTADENA
  })
  assert.equal(layers.before, jpeg)
  assert.equal(layers.overlay, png)
  assert.deepEqual(layers.frames, [jpeg])
  assert.equal(layers.chart, null)
  assert.equal(layers.result, ALTADENA)
  assert.deepEqual(readLayers({ kind: 'timelapse' }), {
    kind: 'timelapse', before: null, after: null, overlay: null, frames: [], chart: null, result: null
  })
  for (const bad of [null, 'x', [], 3]) assert.equal(readLayers(bad), null)
})
